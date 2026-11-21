// Split/Second PC fix - HDR output (HDR=1)
//
// Direct3D 9 has no way to an HDR10 display on Windows, so the finished frame goes out through a DXGI
// swap chain of its own (Direct3D 11, flip model, 10 bits, BT.2020 with the PQ curve):
//  - the game's device is made windowed and its window a borderless one over the whole screen; the
//    game draws its frames as before, but the device's own Present is not called any more,
//  - at Present the back buffer is copied into a texture, one full screen pass converts it to linear
//    light at the paper white level, BT.2020 and PQ into a 10 bit target, which is read back and handed
//    to the DXGI swap chain on the same window.
// The game's code and data are not touched; the hooks are the device's vtable slots (Present, Reset)
// and the device creation that diag.cpp already has.
//
// Stage 1: the picture as the game makes it, in an HDR10 signal with an adjustable paper white.
//
// Stage 2: the light above white. The game renders the scene into a half float target and its filmic
// tone mapper (one pass at the end of cSSBloomEffect, see postfx.cpp) squeezes it into 8 bits: the
// curve T(L) = k L^p / (L^p + q) on the luminance, then the bloom added as a screen blend. Right after
// that pass, with the game's own textures and constants still bound, one more pass of the fix runs the
// same curve twice per pixel: as the game does, and with the top of the curve opened up from a knee on
// (same value and slope there, then rising towards the display's peak instead of flattening out at
// white). The ratio of the two, a gain >= 1 per pixel, goes into a target of the fix. The game's own
// output is not changed; its later passes (colour cube, motion blur, vignette) run on it as before.
// When the first Scaleform movie (the HUD, menus) is drawn, the game's target is copied: the picture
// without the HUD. At Present every pixel that still equals that copy gets the gain, so the HUD and the
// menus stay at paper white and only the scene's highlights go above it.
#include "common.h"
#include <d3d9.h>
#include <d3d11.h>
#include <dxgi1_6.h>
#include <d3dcompiler.h>
#include <stdio.h>
#include <math.h>

typedef HRESULT(WINAPI* CreateDXGIFactory1_t)(REFIID, void**);
typedef HRESULT(WINAPI* D3D11CreateDevice_t)(IDXGIAdapter*, D3D_DRIVER_TYPE, HMODULE, UINT, const D3D_FEATURE_LEVEL*, UINT, UINT,
                                             ID3D11Device**, D3D_FEATURE_LEVEL*, ID3D11DeviceContext**);
typedef HRESULT(WINAPI* D3DCompile_t)(LPCVOID, SIZE_T, LPCSTR, const D3D_SHADER_MACRO*, ID3DInclude*, LPCSTR, LPCSTR, UINT, UINT,
                                      ID3DBlob**, ID3DBlob**);

template <class T> static void Drop(T*& p)
{
    if (p) p->Release();
    p = NULL;
}

static bool s_failed;          // something did not work: the game's own Present from then on
static IDirect3D9* s_d3d;
static UINT s_adapter;
static HWND s_hwnd;
static HMONITOR s_monitor;
static UINT s_width, s_height;
static UINT s_interval;        // the game's wish: 0 = no vsync, 1 = vsync
static IDirect3DDevice9* s_dev;

// DXGI side
static ID3D11Device* s_dev11;
static ID3D11DeviceContext* s_ctx;
static IDXGISwapChain1* s_swap;
static ID3D11Texture2D* s_upload;
static UINT s_swapW, s_swapH;

// Direct3D 9 side
static IDirect3DTexture9* s_sceneTex;
static IDirect3DSurface9* s_sceneSurf;
static IDirect3DSurface9* s_pq;     // 10 bit target in the swap chain's memory layout
static IDirect3DSurface9* s_sys[2]; // the same in system memory, one per frame in turn
static unsigned s_frame;            // frames converted since the targets were made
static IDirect3DStateBlock9* s_state;
static IDirect3DVertexShader9* s_vs;
static IDirect3DPixelShader9* s_ps;
static IDirect3DVertexDeclaration9* s_decl;
static D3DFORMAT s_pqFormat;

// stage 2
static IDirect3DPixelShader9* s_ps2;     // Present: with the gain
static IDirect3DPixelShader9* s_psGain;  // after the tone mapper: the gain
static IDirect3DTexture9* s_gainTex;     // gain per pixel, the size of the game's tone mapping target
static IDirect3DSurface9* s_gainSurf;
static IDirect3DTexture9* s_snapTex;     // the game's target when the first Scaleform movie is drawn
static IDirect3DSurface9* s_snapSurf;
static UINT s_gainW, s_gainH;
static D3DFORMAT s_gainFormat;
static bool s_toneDone;   // this frame: the gain was made
static bool s_snapDone;   // this frame: the picture without the HUD was copied
static bool s_uiHooked;   // the Scaleform hook is in, so a frame without a movie has no HUD
static bool s_movieSeen;  // this frame: a movie after the tone mapper (snapshot or not)
static unsigned s_toneCalls, s_presents, s_snaps, s_stage2;  // counts for the log

static float PeakNits()
{
    return g_cfg.hdrPeak > 0 ? (float)g_cfg.hdrPeak : 1000.0f;
}

// ------------------------------------------------------------------ window and present parameters

void HdrPresentParams(IDirect3D9* d3d, UINT adapter, HWND focus, D3DPRESENT_PARAMETERS* pp)
{
    if (!g_cfg.hdr || s_failed || !pp) return;
    if (d3d) {  // CreateDevice; a Reset passes NULL and keeps what we have
        s_d3d = d3d;
        s_adapter = adapter;
    }
    d3d = s_d3d;
    adapter = s_adapter;
    HWND wnd = pp->hDeviceWindow ? pp->hDeviceWindow : focus ? focus : s_hwnd;
    s_interval = pp->PresentationInterval == D3DPRESENT_INTERVAL_IMMEDIATE ? 0 : 1;
    pp->Windowed = TRUE;
    pp->FullScreen_RefreshRateInHz = 0;
    pp->PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    if (pp->SwapEffect == D3DSWAPEFFECT_FLIP) pp->SwapEffect = D3DSWAPEFFECT_DISCARD;
    s_monitor = d3d ? d3d->GetAdapterMonitor(adapter) : NULL;
    MONITORINFO mi = { sizeof(mi) };
#ifndef HDR_TEST
    if (wnd && s_monitor && GetMonitorInfoA(s_monitor, &mi)) {
        SetWindowLongA(wnd, GWL_STYLE, WS_POPUP | WS_VISIBLE);
        SetWindowLongA(wnd, GWL_EXSTYLE, GetWindowLongA(wnd, GWL_EXSTYLE) & ~(WS_EX_TOPMOST | WS_EX_WINDOWEDGE | WS_EX_CLIENTEDGE));
        SetWindowPos(wnd, HWND_TOP, mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right - mi.rcMonitor.left,
                     mi.rcMonitor.bottom - mi.rcMonitor.top, SWP_FRAMECHANGED | SWP_SHOWWINDOW);
    }
#endif
    if (!pp->BackBufferWidth || !pp->BackBufferHeight) {
        RECT rc;
        GetClientRect(wnd, &rc);
        pp->BackBufferWidth = rc.right - rc.left;
        pp->BackBufferHeight = rc.bottom - rc.top;
    }
    s_hwnd = wnd;
    s_width = pp->BackBufferWidth;
    s_height = pp->BackBufferHeight;
    Log("HDR: device windowed (borderless %ldx%ld), back buffer %ux%u, %s", mi.rcMonitor.right - mi.rcMonitor.left,
        mi.rcMonitor.bottom - mi.rcMonitor.top, s_width, s_height, s_interval ? "vsync" : "no vsync");
}

// ------------------------------------------------------------------ DXGI swap chain

static bool MakeSwapChain()
{
    HMODULE dxgi = LoadLibraryA("dxgi.dll"), d3d11 = LoadLibraryA("d3d11.dll");
    CreateDXGIFactory1_t createFactory = dxgi ? (CreateDXGIFactory1_t)GetProcAddress(dxgi, "CreateDXGIFactory1") : NULL;
    D3D11CreateDevice_t createDevice = d3d11 ? (D3D11CreateDevice_t)GetProcAddress(d3d11, "D3D11CreateDevice") : NULL;
    if (!createFactory || !createDevice) {
        Log("HDR: no DXGI / Direct3D 11");
        return false;
    }
    IDXGIFactory2* factory = NULL;
    if (FAILED(createFactory(__uuidof(IDXGIFactory2), (void**)&factory))) {
        Log("HDR: DXGI 1.2 factory not available");
        return false;
    }
    // the adapter that drives the game's monitor
    IDXGIAdapter1* adapter = NULL;
    IDXGIOutput6* output = NULL;
    for (UINT i = 0; !output; i++) {
        IDXGIAdapter1* a = NULL;
        if (factory->EnumAdapters1(i, &a) != S_OK) break;
        for (UINT j = 0; !output; j++) {
            IDXGIOutput* o = NULL;
            if (a->EnumOutputs(j, &o) != S_OK) break;
            DXGI_OUTPUT_DESC od;
            if (SUCCEEDED(o->GetDesc(&od)) && od.Monitor == s_monitor) {
                o->QueryInterface(__uuidof(IDXGIOutput6), (void**)&output);
                if (output) {
                    adapter = a;
                    a->AddRef();
                }
            }
            o->Release();
        }
        a->Release();
    }
    if (output) {
        DXGI_OUTPUT_DESC1 d;
        if (SUCCEEDED(output->GetDesc1(&d))) {
            Log("HDR: display %ls, %s, %u bits, peak %.0f nits, full screen %.0f nits", d.DeviceName,
                d.ColorSpace == DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020 ? "HDR on in Windows" : "HDR off in Windows (SDR)",
                d.BitsPerColor, d.MaxLuminance, d.MaxFullFrameLuminance);
            if (d.ColorSpace != DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020)
                Log("HDR: switch HDR on in the Windows display settings, the picture stays SDR until then");
        }
        output->Release();
    }
    static const D3D_FEATURE_LEVEL levels[] = { D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_1, D3D_FEATURE_LEVEL_10_0 };
    HRESULT hr = createDevice(adapter, adapter ? D3D_DRIVER_TYPE_UNKNOWN : D3D_DRIVER_TYPE_HARDWARE, NULL, 0, levels, 3,
                              D3D11_SDK_VERSION, &s_dev11, NULL, &s_ctx);
    if (adapter) adapter->Release();
    if (FAILED(hr)) {
        Log("HDR: Direct3D 11 device failed (%08lx)", hr);
        factory->Release();
        return false;
    }
    DXGI_SWAP_CHAIN_DESC1 sd = {};
    sd.Width = s_width;
    sd.Height = s_height;
    sd.Format = DXGI_FORMAT_R10G10B10A2_UNORM;
    sd.SampleDesc.Count = 1;
    sd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    sd.BufferCount = 2;
    sd.Scaling = DXGI_SCALING_STRETCH;
    sd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    sd.AlphaMode = DXGI_ALPHA_MODE_IGNORE;
    hr = factory->CreateSwapChainForHwnd(s_dev11, s_hwnd, &sd, NULL, NULL, &s_swap);
    if (FAILED(hr)) {
        Log("HDR: swap chain on the game's window failed (%08lx)", hr);
        factory->Release();
        return false;
    }
    factory->MakeWindowAssociation(s_hwnd, DXGI_MWA_NO_ALT_ENTER | DXGI_MWA_NO_WINDOW_CHANGES);
    factory->Release();
    s_swapW = s_width;
    s_swapH = s_height;
    IDXGISwapChain3* sc3 = NULL;
    UINT support = 0;
    if (SUCCEEDED(s_swap->QueryInterface(__uuidof(IDXGISwapChain3), (void**)&sc3)) &&
        SUCCEEDED(sc3->CheckColorSpaceSupport(DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020, &support)) &&
        (support & DXGI_SWAP_CHAIN_COLOR_SPACE_SUPPORT_FLAG_PRESENT) &&
        SUCCEEDED(sc3->SetColorSpace1(DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020))) {
        IDXGISwapChain4* sc4 = NULL;
        if (SUCCEEDED(s_swap->QueryInterface(__uuidof(IDXGISwapChain4), (void**)&sc4))) {
            DXGI_HDR_METADATA_HDR10 md = {};
            md.RedPrimary[0] = 35400; md.RedPrimary[1] = 14600;  // BT.2020, units of 0.00002
            md.GreenPrimary[0] = 8500; md.GreenPrimary[1] = 39850;
            md.BluePrimary[0] = 6550; md.BluePrimary[1] = 2300;
            md.WhitePoint[0] = 15635; md.WhitePoint[1] = 16450;
            md.MaxMasteringLuminance = (UINT)PeakNits();
            md.MinMasteringLuminance = 1;  // 0.0001 nits
            md.MaxContentLightLevel = (UINT16)PeakNits();
            md.MaxFrameAverageLightLevel = (UINT16)g_cfg.hdrPaperWhite;
            sc4->SetHDRMetaData(DXGI_HDR_METADATA_TYPE_HDR10, sizeof(md), &md);
            sc4->Release();
        }
        Log("HDR: swap chain %ux%u, HDR10 (PQ, BT.2020), paper white %d nits, peak %.0f nits", s_width, s_height,
            g_cfg.hdrPaperWhite, PeakNits());
    } else {
        Log("HDR: the swap chain does not take HDR10 (support %u), the picture goes out as it is", support);
    }
    if (sc3) sc3->Release();
    return true;
}

static bool MakeUploadTexture()
{
    Drop(s_upload);
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = s_width;
    td.Height = s_height;
    td.MipLevels = 1;
    td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R10G10B10A2_UNORM;
    td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DYNAMIC;
    td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    td.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    HRESULT hr = s_dev11->CreateTexture2D(&td, NULL, &s_upload);
    if (FAILED(hr)) Log("HDR: upload texture failed (%08lx)", hr);
    return SUCCEEDED(hr);
}

// ------------------------------------------------------------------ the conversion pass (Direct3D 9)

static const char kShader[] =
    "float4 halfPixel : register(c0);\n"
    "struct V { float4 p : POSITION; float2 t : TEXCOORD0; };\n"
    "V vs(V v) { v.p.xy += halfPixel.xy; return v; }\n"
    "sampler2D scene : register(s0);\n"
    "sampler2D gain : register(s1);\n"
    "sampler2D snap : register(s2);\n"
    "float4 k : register(c0);  // x paper white / 10000, z gamma of the game's picture, w peak / paper white\n"
    "float3 PQ(float3 y) {\n"
    "    y = pow(max(y, 0.0), 0.1593017578125);\n"
    "    return pow((0.8359375 + 18.8515625 * y) / (1.0 + 18.6875 * y), 78.84375);\n"
    "}\n"
    "float4 ps(float2 t : TEXCOORD0) : COLOR {\n"
    "    float3 v = tex2D(scene, t).rgb;\n"
    "    float3 c = pow(max(v, 0.0), k.z);\n"
    "#if STAGE2\n"
    "    float3 d = abs(v - tex2D(snap, t).rgb);\n"
    "    if (max(d.r, max(d.g, d.b)) < 1.5 / 255.0) c = min(c * tex2D(gain, t).r, k.w);\n"
    "#endif\n"
    "    float3 w;\n"
    "    w.x = dot(c, float3(0.6274040, 0.3292820, 0.0433136));\n"
    "    w.y = dot(c, float3(0.0690970, 0.9195400, 0.0113612));\n"
    "    w.z = dot(c, float3(0.0163916, 0.0880132, 0.8955950));\n"
    "    w = PQ(w * k.x);\n"
    "#if SWAP_RB\n"
    "    return float4(w.bgr, 1.0);\n"
    "#else\n"
    "    return float4(w, 1.0);\n"
    "#endif\n"
    "}\n";

// The gain, right after the game's tone mapper with its inputs: the scene (half float), the average
// luminance (1x1) and the bloom. The game's pass: e = Consts.y (Consts.x AveLum - 1) + 1, c = e scene,
// L = luminance(c), T = Consts2 L^Consts.z / (L^Consts.z + Consts.w), out = c T / L (1 - b) + b with the
// bloom b = saturate(bloom^Curve). The opened curve is the same up to the knee s0 (log2 of the luminance
// where T reaches the knee value). Above it, in stops of scene and display light (u = log2 of the linear
// display value), u0 + R tanh(a (s - s0) / R): the game curve's value and slope at the knee, rising as
// the scene does and levelling off at the peak (R stops above the knee). The gain has two parts: the
// opened curve against the game's, and in the shoulder only, the light the 8 bit output clipped off
// (so a saturated colour in the mid tones stays as the game grades it).
static const char kGainShader[] =
    "sampler2D scene : register(s0);\n"
    "sampler2D aveLum : register(s1);\n"
    "sampler2D bloom : register(s2);\n"
    "float4 f : register(c0);  // the game's SSFilmicToneMapper_Consts\n"
    "float4 g : register(c1);  // x the game's Consts2, y the bloom curve, z 1 = bloom added, w 1 / (top - knee)\n"
    "float4 h : register(c2);  // x s0, y u0, z a / R, w R\n"
    "float4 sz : register(c3); // xy 1 / scene size, z the knee (gamma value)\n"
    "static const float3 kLum = float3(0.2125, 0.7154, 0.0721);\n"
    "float4 ps(float2 vpos : VPOS) : COLOR {\n"
    "    float2 uv = (vpos + 0.5) * sz.xy;\n"
    "    float e = f.y * (f.x * tex2Dlod(aveLum, float4(0.5, 0.5, 0, 0)).x - 1.0) + 1.0;\n"
    "    float3 c = max(e * tex2Dlod(scene, float4(uv, 0, 0)).rgb, 0.0);\n"
    "    float L = max(dot(c, kLum), 1e-6);\n"
    "    float lp = pow(L, f.z);\n"
    "    float T = g.x * lp / (lp + f.w);\n"
    "    float s = log2(L);\n"
    "    float Th = s > h.x ? exp2((h.y + h.w * tanh(h.z * (s - h.x))) / 2.2) : T;\n"
    "    float3 b = 0;\n"
    "    if (g.z > 0.5) b = saturate(exp2(log2(max(tex2Dlod(bloom, float4(uv, 0, 0)).rgb, 1e-6)) * g.y));\n"
    "    float3 os = max(c * (T / L) * (1.0 - b) + b, 0.0);\n"
    "    float3 oh = max(c * (Th / L) * (1.0 - b) + b, 0.0);\n"
    "    float ys = dot(pow(os, 2.2), kLum);\n"
    "    float yc = dot(pow(saturate(os), 2.2), kLum);\n"
    "    float yh = dot(pow(oh, 2.2), kLum);\n"
    "    float top = max(T, max(b.r, max(b.g, b.b)));\n"
    "    float w = saturate((top - sz.z) * g.w);  // clip recovery: 0 at the knee, 1 at the curve's top\n"
    "    float G = yh / max(ys, 1e-5) * pow(max(ys / max(yc, 1e-5), 1.0), w);\n"
    "    return float4(clamp(G, 1.0, 64.0).xxx, 1.0);\n"
    "}\n";

static D3DCompile_t Compiler()
{
    static D3DCompile_t compile;
    if (!compile) {
        HMODULE comp = LoadLibraryA("d3dcompiler_47.dll");
        compile = comp ? (D3DCompile_t)GetProcAddress(comp, "D3DCompile") : NULL;
    }
    return compile;
}

static IDirect3DPixelShader9* CompilePS(IDirect3DDevice9* dev, const char* src, size_t len, const D3D_SHADER_MACRO* defs)
{
    ID3DBlob *code = NULL, *err = NULL;
    IDirect3DPixelShader9* ps = NULL;
    if (SUCCEEDED(Compiler()(src, len, "hdr", defs, NULL, "ps", "ps_3_0", 0, 0, &code, &err)))
        dev->CreatePixelShader((const DWORD*)code->GetBufferPointer(), &ps);
    else
        Log("HDR: shader: %s", err ? (const char*)err->GetBufferPointer() : "failed");
    Drop(code);
    Drop(err);
    return ps;
}

static bool MakeShaders(IDirect3DDevice9* dev)
{
    if (s_ps) return true;
    D3DCompile_t compile = Compiler();
    if (!compile) {
        Log("HDR: d3dcompiler_47.dll not found");
        return false;
    }
    const char* swap = s_pqFormat == D3DFMT_A2R10G10B10 ? "1" : "0";
    D3D_SHADER_MACRO defs[] = { { "SWAP_RB", swap }, { "STAGE2", "0" }, { NULL, NULL } };
    D3D_SHADER_MACRO defs2[] = { { "SWAP_RB", swap }, { "STAGE2", "1" }, { NULL, NULL } };
    ID3DBlob *vsCode = NULL, *err = NULL;
    HRESULT hr = compile(kShader, sizeof(kShader) - 1, "hdr", defs, NULL, "vs", "vs_3_0", 0, 0, &vsCode, &err);
    if (FAILED(hr)) {
        Log("HDR: shader: %s", err ? (const char*)err->GetBufferPointer() : "failed");
        Drop(err);
        return false;
    }
    dev->CreateVertexShader((const DWORD*)vsCode->GetBufferPointer(), &s_vs);
    Drop(vsCode);
    s_ps = CompilePS(dev, kShader, sizeof(kShader) - 1, defs);
    s_ps2 = CompilePS(dev, kShader, sizeof(kShader) - 1, defs2);
    s_psGain = CompilePS(dev, kGainShader, sizeof(kGainShader) - 1, NULL);
    if (!s_ps2 || !s_psGain) Log("HDR: no stage 2 shaders, the picture stays at stage 1");
    static const D3DVERTEXELEMENT9 decl[] = {
        { 0, 0, D3DDECLTYPE_FLOAT4, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_POSITION, 0 },
        { 0, 16, D3DDECLTYPE_FLOAT2, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_TEXCOORD, 0 },
        D3DDECL_END(),
    };
    dev->CreateVertexDeclaration(decl, &s_decl);
    return s_vs && s_ps && s_decl;
}

static bool MakeTargets(IDirect3DDevice9* dev)
{
    D3DDISPLAYMODE dm;
    if (!s_pqFormat) {
        s_pqFormat = D3DFMT_A2B10G10R10;  // the same bit order as DXGI's R10G10B10A2
        if (s_d3d && SUCCEEDED(s_d3d->GetAdapterDisplayMode(s_adapter, &dm)) &&
            FAILED(s_d3d->CheckDeviceFormat(s_adapter, D3DDEVTYPE_HAL, dm.Format, D3DUSAGE_RENDERTARGET, D3DRTYPE_SURFACE,
                                            D3DFMT_A2B10G10R10)))
            s_pqFormat = D3DFMT_A2R10G10B10;  // red and blue swapped in the shader
    }
    HRESULT hr = dev->CreateTexture(s_width, s_height, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &s_sceneTex, NULL);
    if (SUCCEEDED(hr)) hr = s_sceneTex->GetSurfaceLevel(0, &s_sceneSurf);
    if (SUCCEEDED(hr)) hr = dev->CreateRenderTarget(s_width, s_height, s_pqFormat, D3DMULTISAMPLE_NONE, 0, FALSE, &s_pq, NULL);
    for (int i = 0; i < 2 && SUCCEEDED(hr); i++)
        hr = dev->CreateOffscreenPlainSurface(s_width, s_height, s_pqFormat, D3DPOOL_SYSTEMMEM, &s_sys[i], NULL);
    s_frame = 0;
    if (SUCCEEDED(hr)) hr = dev->CreateStateBlock(D3DSBT_ALL, &s_state);
    if (FAILED(hr)) Log("HDR: targets %ux%u failed (%08lx)", s_width, s_height, hr);
    return SUCCEEDED(hr);
}

static void DropGain()
{
    Drop(s_gainSurf);
    Drop(s_gainTex);
    Drop(s_snapSurf);
    Drop(s_snapTex);
    s_gainW = s_gainH = 0;
    s_toneDone = s_snapDone = s_movieSeen = false;
}

static void DropTargets()
{
    DropGain();
    Drop(s_state);
    Drop(s_sys[0]);
    Drop(s_sys[1]);
    Drop(s_pq);
    Drop(s_sceneSurf);
    Drop(s_sceneTex);
}

static void Fail(const char* why)
{
    Log("HDR: %s, the game's own output from now on", why);
    s_failed = true;
}

void HdrDeviceCreated(IDirect3DDevice9* dev)
{
    if (!g_cfg.hdr || s_failed || !dev || !s_hwnd) return;
    s_dev = dev;
    if (!s_swap && !MakeSwapChain()) return Fail("no swap chain");
    if (!MakeUploadTexture()) return Fail("no upload texture");
    if (!MakeTargets(dev)) return Fail("no targets");
    if (!MakeShaders(dev)) return Fail("no shaders");
}

void HdrDeviceLost()
{
    DropTargets();
}

void HdrDeviceReset(IDirect3DDevice9* dev)
{
    if (!g_cfg.hdr || s_failed || !s_swap) return;
    if (s_width != s_swapW || s_height != s_swapH) {
        s_ctx->ClearState();
        s_ctx->Flush();
        HRESULT hr = s_swap->ResizeBuffers(2, s_width, s_height, DXGI_FORMAT_R10G10B10A2_UNORM, 0);
        if (FAILED(hr)) return Fail("swap chain resize failed");
        s_swapW = s_width;
        s_swapH = s_height;
        if (!MakeUploadTexture()) return Fail("no upload texture");
    }
    if (!MakeTargets(dev)) Fail("no targets after reset");
}

// ------------------------------------------------------------------ per frame

// called instead of the device's Present; false = not active, the game's Present runs
bool HdrPresent(IDirect3DDevice9* dev, HRESULT* result)
{
    if (!g_cfg.hdr || s_failed || !s_swap || !s_sys[1] || dev != s_dev) return false;
    HRESULT coop = dev->TestCooperativeLevel();
    if (coop != D3D_OK) {
        *result = coop == D3DERR_DEVICENOTRESET ? D3DERR_DEVICELOST : coop;  // the game resets on lost
        return true;
    }
    LARGE_INTEGER start;
    QueryPerformanceCounter(&start);
    IDirect3DSurface9 *back = NULL, *rt0 = NULL, *ds = NULL;
    if (FAILED(dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &back))) return false;
    dev->GetRenderTarget(0, &rt0);
    dev->GetDepthStencilSurface(&ds);
    s_state->Capture();
    SaveStage0Tracking();

    dev->StretchRect(back, NULL, s_sceneSurf, NULL, D3DTEXF_POINT);
    dev->SetRenderTarget(0, s_pq);
    for (DWORD i = 1; i < 4; i++) dev->SetRenderTarget(i, NULL);
    dev->SetDepthStencilSurface(NULL);
    D3DVIEWPORT9 vp = { 0, 0, s_width, s_height, 0.0f, 1.0f };
    dev->SetViewport(&vp);
    // stage 2 when the gain was made this frame; without a Scaleform movie in the frame (HUD off) the whole
    // picture is the scene, unless the hook could not go in
    bool stage2 = s_ps2 && s_toneDone && (s_snapDone || (s_uiHooked && !s_movieSeen));
    s_presents++;
    if (stage2) s_stage2++;
    dev->SetVertexShader(s_vs);
    dev->SetPixelShader(stage2 ? s_ps2 : s_ps);
    dev->SetVertexDeclaration(s_decl);
    dev->SetTexture(0, s_sceneTex);
    if (stage2) {
        dev->SetTexture(1, s_gainTex);
        dev->SetTexture(2, s_snapDone ? s_snapTex : s_sceneTex);
    }
    bool same = s_gainW == s_width && s_gainH == s_height;
    for (DWORD i = 0; i < 3; i++) {
        DWORD filter = i == 0 || same ? D3DTEXF_POINT : D3DTEXF_LINEAR;
        dev->SetSamplerState(i, D3DSAMP_MINFILTER, filter);
        dev->SetSamplerState(i, D3DSAMP_MAGFILTER, filter);
        dev->SetSamplerState(i, D3DSAMP_MIPFILTER, D3DTEXF_NONE);
        dev->SetSamplerState(i, D3DSAMP_ADDRESSU, D3DTADDRESS_CLAMP);
        dev->SetSamplerState(i, D3DSAMP_ADDRESSV, D3DTADDRESS_CLAMP);
        dev->SetSamplerState(i, D3DSAMP_SRGBTEXTURE, FALSE);
    }
    s_toneDone = s_snapDone = s_movieSeen = false;
    dev->SetRenderState(D3DRS_ZENABLE, FALSE);
    dev->SetRenderState(D3DRS_ZWRITEENABLE, FALSE);
    dev->SetRenderState(D3DRS_ALPHABLENDENABLE, FALSE);
    dev->SetRenderState(D3DRS_ALPHATESTENABLE, FALSE);
    dev->SetRenderState(D3DRS_STENCILENABLE, FALSE);
    dev->SetRenderState(D3DRS_SCISSORTESTENABLE, FALSE);
    dev->SetRenderState(D3DRS_CULLMODE, D3DCULL_NONE);
    dev->SetRenderState(D3DRS_SRGBWRITEENABLE, FALSE);
    dev->SetRenderState(D3DRS_COLORWRITEENABLE, 0xF);
    dev->SetRenderState(D3DRS_FILLMODE, D3DFILL_SOLID);
    float halfPixel[4] = { -1.0f / s_width, 1.0f / s_height, 0, 0 };
    dev->SetVertexShaderConstantF(0, halfPixel, 1);
    float k[4] = { g_cfg.hdrPaperWhite / 10000.0f, PeakNits() / 10000.0f, 2.2f, PeakNits() / g_cfg.hdrPaperWhite };
    dev->SetPixelShaderConstantF(0, k, 1);
    static const float quad[4][6] = {
        { -1, 1, 0, 1, 0, 0 }, { 1, 1, 0, 1, 1, 0 }, { -1, -1, 0, 1, 0, 1 }, { 1, -1, 0, 1, 1, 1 },
    };
    dev->BeginScene();
    dev->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, quad, sizeof(quad[0]));
    dev->EndScene();

    s_state->Apply();
    RestoreStage0Tracking();
    dev->SetRenderTarget(0, rt0);
    dev->SetDepthStencilSurface(ds);
    Drop(rt0);
    Drop(ds);
    Drop(back);

    // to the DXGI swap chain
    LARGE_INTEGER t[4];  // pass submitted, read back, copied, presented
    QueryPerformanceCounter(&t[0]);
    // The read back is queued here and taken one frame later: by then the GPU has finished it, so the
    // CPU does not wait for the whole frame to be drawn (that cost 6 to 10 ms at 4K). One frame of delay.
    IDirect3DSurface9* fill = s_sys[s_frame & 1];
    IDirect3DSurface9* take = s_sys[(s_frame + 1) & 1];
    HRESULT hr = dev->GetRenderTargetData(s_pq, fill);
    if (SUCCEEDED(hr) && s_frame++ == 0) hr = dev->GetRenderTargetData(s_pq, take);  // the very first frame: both
    QueryPerformanceCounter(&t[1]);
    t[2] = t[3] = t[1];
    D3DLOCKED_RECT lr;
    if (SUCCEEDED(hr) && SUCCEEDED(take->LockRect(&lr, NULL, D3DLOCK_READONLY))) {
        D3D11_MAPPED_SUBRESOURCE m;
        if (SUCCEEDED(s_ctx->Map(s_upload, 0, D3D11_MAP_WRITE_DISCARD, 0, &m))) {
            const BYTE* src = (const BYTE*)lr.pBits;
            BYTE* dst = (BYTE*)m.pData;
            UINT row = s_width * 4;
            for (UINT y = 0; y < s_height; y++) memcpy(dst + y * m.RowPitch, src + y * lr.Pitch, row);
            s_ctx->Unmap(s_upload, 0);
        }
        take->UnlockRect();
        ID3D11Texture2D* buf = NULL;
        if (SUCCEEDED(s_swap->GetBuffer(0, __uuidof(ID3D11Texture2D), (void**)&buf))) {
            s_ctx->CopyResource(buf, s_upload);
            buf->Release();
        }
        QueryPerformanceCounter(&t[2]);
        hr = s_swap->Present(s_interval, 0);
        QueryPerformanceCounter(&t[3]);
        if (FAILED(hr)) {
            static int logged;
            if (logged++ < 5) Log("HDR: Present %08lx", hr);
        }
    }
    *result = D3D_OK;
    if (g_cfg.log) {  // what the extra pass and the copy cost, every 10 s
        static unsigned frames;
        static LONGLONG spent, worst, since, part[4];
        static LARGE_INTEGER freq;
        LARGE_INTEGER now;
        QueryPerformanceCounter(&now);
        if (!freq.QuadPart) QueryPerformanceFrequency(&freq);
        LONGLONG took = now.QuadPart - start.QuadPart;
        spent += took;
        if (took > worst) worst = took;
        part[0] += t[0].QuadPart - start.QuadPart;
        for (int i = 1; i < 4; i++) part[i] += t[i].QuadPart - t[i - 1].QuadPart;
        if (frames++ == 0) {
            since = now.QuadPart;
            Log("HDR: first frame out");
        } else if (now.QuadPart - since >= freq.QuadPart * 10) {
            double ms = 1000.0 / freq.QuadPart / frames;
            Log("HDR: output %.2f ms a frame on average, %.2f ms at most (%u frames): pass %.2f, read back %.2f, copy %.2f, "
                "present %.2f", spent * ms, worst * 1000.0 / freq.QuadPart, frames, part[0] * ms, part[1] * ms, part[2] * ms,
                part[3] * ms);
            frames = 1;
            spent = worst = 0;
            part[0] = part[1] = part[2] = part[3] = 0;
            since = now.QuadPart;
        }
    }
    return true;
}

// ------------------------------------------------------------------ stage 2: the gain after the tone mapper

// What a pixel shader of the game reads, from the constant table the compiler put into its code.
struct ToneShader {
    IDirect3DPixelShader9* ps;  // not referenced, only compared
    bool usable;
    int consts, consts2, curve;  // float registers, curve -1 = no bloom
    int scene, aveLum, bloom;    // samplers, bloom -1 = none
};

static bool ReadToneShader(IDirect3DPixelShader9* ps, ToneShader* out)
{
    *out = ToneShader();
    out->ps = ps;
    out->consts = out->consts2 = out->curve = out->scene = out->aveLum = out->bloom = -1;
    UINT size = 0;
    if (FAILED(ps->GetFunction(NULL, &size)) || size < 8 || size > (1u << 20)) return false;
    DWORD* code = (DWORD*)malloc(size);
    bool ok = code && SUCCEEDED(ps->GetFunction(code, &size));
    UINT n = size / 4;
    bool found = false;
    for (UINT i = 1; ok && i < n && !found;) {
        DWORD tok = code[i];
        if ((tok & 0xFFFF) != 0xFFFE) break;  // the comments come first
        UINT len = tok >> 16;
        if (i + 1 + len > n) break;
        const BYTE* data = (const BYTE*)&code[i + 1];
        if (len >= 8 && *(const DWORD*)data == MAKEFOURCC('C', 'T', 'A', 'B')) {
            const BYTE* tab = data + 4;
            UINT tabSize = len * 4 - 4;
            DWORD count = *(const DWORD*)(tab + 12), info = *(const DWORD*)(tab + 16);
            if (info + count * 20 > tabSize) break;
            found = true;
            for (DWORD c = 0; c < count; c++) {
                const BYTE* ci = tab + info + c * 20;
                DWORD nameAt = *(const DWORD*)ci;
                WORD set = *(const WORD*)(ci + 4), reg = *(const WORD*)(ci + 6);
                if (nameAt >= tabSize) return free(code), false;
                const char* name = (const char*)tab + nameAt;
                if (!memchr(name, 0, tabSize - nameAt)) return free(code), false;
                int* slot = !strcmp(name, "SSFilmicToneMapper_Consts") ? &out->consts
                          : !strcmp(name, "SSFilmicToneMapper_Consts2") ? &out->consts2
                          : !strcmp(name, "SSBloomAdd_Curve") ? &out->curve
                          : !strcmp(name, "PosUVUVPassThru_texture") ? &out->scene
                          : !strcmp(name, "SSFilmicToneMapper_AveLum") ? &out->aveLum
                          : !strcmp(name, "SSBloomAdd_tex") ? &out->bloom : NULL;
                bool sampler = slot == &out->scene || slot == &out->aveLum || slot == &out->bloom;
                if (!slot || set != (sampler ? 3 : 2)) {  // anything else in it: a pass not known here
                    if (g_cfg.log) Log("HDR: tone mapping shader %p also reads %s, left out", ps, name);
                    free(code);
                    return false;
                }
                *slot = reg;
            }
        }
        i += 1 + len;
    }
    free(code);
    out->usable = found && out->consts >= 0 && out->consts2 >= 0 && out->scene >= 0 && out->aveLum >= 0 &&
                  (out->bloom >= 0) == (out->curve >= 0);
    return out->usable;
}

static const ToneShader* FindToneShader(IDirect3DPixelShader9* ps)
{
    static ToneShader known[8];
    static int count;
    for (int i = 0; i < count; i++)
        if (known[i].ps == ps) return &known[i];
    static ToneShader unknown;  // once the cache is full: not recognised, without reading the shader each frame
    if (count == 8) return &unknown;
    ToneShader* t = &known[count++];
    bool ok = ReadToneShader(ps, t);
    Log("HDR: tone mapping shader %p %s (consts c%d c%d, curve c%d, scene s%d, average s%d, bloom s%d)", ps,
        ok ? "recognised" : "not recognised", t->consts, t->consts2, t->curve, t->scene, t->aveLum, t->bloom);
    return t;
}

static bool IsFloatFormat(D3DFORMAT f)
{
    return f == D3DFMT_A16B16G16R16F || f == D3DFMT_A32B32G32R32F || f == D3DFMT_G16R16F || f == D3DFMT_R16F ||
           f == D3DFMT_R32F || f == D3DFMT_G32R32F;
}

static bool TextureSize(IDirect3DBaseTexture9* t, D3DSURFACE_DESC* d)
{
    return t && t->GetType() == D3DRTYPE_TEXTURE && SUCCEEDED(((IDirect3DTexture9*)t)->GetLevelDesc(0, d));
}

static bool MakeGain(IDirect3DDevice9* dev, UINT w, UINT h)
{
    if (s_gainTex && s_gainW == w && s_gainH == h) return true;
    DropGain();
    if (!s_gainFormat) {
        D3DDISPLAYMODE dm;
        s_gainFormat = D3DFMT_A16B16G16R16F;
        if (s_d3d && SUCCEEDED(s_d3d->GetAdapterDisplayMode(s_adapter, &dm)) &&
            SUCCEEDED(s_d3d->CheckDeviceFormat(s_adapter, D3DDEVTYPE_HAL, dm.Format, D3DUSAGE_RENDERTARGET, D3DRTYPE_TEXTURE,
                                               D3DFMT_R16F)))
            s_gainFormat = D3DFMT_R16F;
    }
    HRESULT hr = dev->CreateTexture(w, h, 1, D3DUSAGE_RENDERTARGET, s_gainFormat, D3DPOOL_DEFAULT, &s_gainTex, NULL);
    if (SUCCEEDED(hr)) hr = s_gainTex->GetSurfaceLevel(0, &s_gainSurf);
    if (SUCCEEDED(hr)) hr = dev->CreateTexture(w, h, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &s_snapTex, NULL);
    if (SUCCEEDED(hr)) hr = s_snapTex->GetSurfaceLevel(0, &s_snapSurf);
    if (FAILED(hr)) {
        Log("HDR: gain targets %ux%u failed (%08lx)", w, h, hr);
        DropGain();
        return false;
    }
    s_gainW = w;
    s_gainH = h;
    Log("HDR: gain targets %ux%u (format %d)", w, h, s_gainFormat);
    return true;
}

// The opened curve's constants (shader c2) from the game's p, q and k; false = nothing above white to show.
static bool CurveConstants(float p, float q, float k, float out[4], float shoulder[2])
{
    const float knee = 0.75f;  // gamma value of the game's curve where the opening starts
    float ymax = PeakNits() / (float)g_cfg.hdrPaperWhite;
    float t0 = knee * (k < 1.0f ? k : 1.0f);
    float y0 = powf(t0, 2.2f);
    if (!(p > 0.0f) || !(q > 0.0f) || !(k > t0) || ymax <= y0 * 1.05f) return false;
    float lp = q * t0 / (k - t0);  // L^p at the knee
    float s0 = log2f(lp) / p;
    float a = 2.2f * p * q / (lp + q);  // d log2(y) / d log2(L) at the knee: the game curve's slope in stops
    float R = log2f(ymax / y0);         // stops from the knee to the peak
    out[0] = s0;
    out[1] = log2f(y0);
    out[2] = a / R;
    out[3] = R;
    shoulder[0] = t0;
    shoulder[1] = 1.0f / (k - t0);
    return true;
}

static void NoteToneMap(IDirect3DDevice9* dev, const ToneShader* t, IDirect3DSurface9* rt)
{
    static int noted;
    if (!g_cfg.log || noted >= 2) return;
    noted++;
    int regs[3] = { t->scene, t->aveLum, t->bloom };
    for (int i = 0; i < 3; i++) {
        IDirect3DBaseTexture9* tex = NULL;
        D3DSURFACE_DESC d = {};
        if (regs[i] >= 0) dev->GetTexture(regs[i], &tex);
        if (TextureSize(tex, &d)) Log("HDR: tone mapping s%d: %ux%u format %d", regs[i], d.Width, d.Height, d.Format);
        if (tex) tex->Release();
    }
    float c[4];
    int fr[3] = { t->consts, t->consts2, t->curve };
    for (int i = 0; i < 3; i++)
        if (fr[i] >= 0 && SUCCEEDED(dev->GetPixelShaderConstantF(fr[i], c, 1)))
            Log("HDR: tone mapping c%d = %.4f %.4f %.4f %.4f", fr[i], c[0], c[1], c[2], c[3]);
    D3DSURFACE_DESC d;
    if (rt && SUCCEEDED(rt->GetDesc(&d))) Log("HDR: tone mapping target %ux%u format %d", d.Width, d.Height, d.Format);
}

// right after the game's tone mapping pass (postfx.cpp's bloom hook), with its state still set
void HdrToneMapped()
{
    IDirect3DDevice9* dev = s_dev;
    // without the Scaleform hook the HUD cannot be told apart and the gain is never used (Present)
    if (!g_cfg.hdr || s_failed || !dev || !s_psGain || !s_state || !s_swap || !s_uiHooked) return;
    IDirect3DPixelShader9* ps = NULL;
    dev->GetPixelShader(&ps);
    if (!ps) return;
    ps->Release();  // only compared; the game holds it
    const ToneShader* t = FindToneShader(ps);
    if (!t->usable) return;

    IDirect3DBaseTexture9 *scene = NULL, *ave = NULL, *bloom = NULL;
    IDirect3DSurface9 *rt0 = NULL, *ds = NULL;
    D3DSURFACE_DESC sd = {}, bd = {}, rd = {};
    dev->GetTexture(t->scene, &scene);
    dev->GetTexture(t->aveLum, &ave);
    if (t->bloom >= 0) dev->GetTexture(t->bloom, &bloom);
    dev->GetRenderTarget(0, &rt0);
    NoteToneMap(dev, t, rt0);
    float fc[4], gc[4] = {}, hc[4], cc[4] = { 1, 1, 1, 1 }, sz[4] = {};
    bool ok = TextureSize(scene, &sd) && IsFloatFormat(sd.Format) && ave && (t->bloom < 0 || bloom) && rt0 &&
              SUCCEEDED(rt0->GetDesc(&rd)) && (t->bloom < 0 || TextureSize(bloom, &bd)) &&
              SUCCEEDED(dev->GetPixelShaderConstantF(t->consts, fc, 1)) &&
              SUCCEEDED(dev->GetPixelShaderConstantF(t->consts2, gc, 1)) &&
              (t->curve < 0 || SUCCEEDED(dev->GetPixelShaderConstantF(t->curve, cc, 1)));
    if (ok) {
        float shoulder[2];
        ok = CurveConstants(fc[2], fc[3], gc[0], hc, shoulder);
        gc[1] = cc[0];
        gc[2] = t->bloom >= 0 ? 1.0f : 0.0f;
        gc[3] = ok ? shoulder[1] : 0.0f;
        sz[2] = ok ? shoulder[0] : 0.0f;
    }
    // a second tone mapping in the frame (split screen) must draw into the same target size
    if (ok && s_toneDone && (rd.Width != s_gainW || rd.Height != s_gainH)) ok = false;
    if (ok) ok = MakeGain(dev, rd.Width, rd.Height);
    if (ok) {
        D3DVIEWPORT9 vp;
        RECT scissor;
        dev->GetViewport(&vp);
        dev->GetScissorRect(&scissor);
        s_toneCalls++;
        dev->GetDepthStencilSurface(&ds);
        s_state->Capture();
        SaveStage0Tracking();
        dev->SetRenderTarget(0, s_gainSurf);
        for (DWORD i = 1; i < 4; i++) dev->SetRenderTarget(i, NULL);
        dev->SetDepthStencilSurface(NULL);
        if (!s_toneDone) {  // the first tone mapping of the frame: no gain where it does not draw
            dev->SetRenderState(D3DRS_SCISSORTESTENABLE, FALSE);
            dev->SetRenderState(D3DRS_COLORWRITEENABLE, 0xF);
            dev->Clear(0, NULL, D3DCLEAR_TARGET, 0xFFFFFFFF, 1.0f, 0);
        }
        dev->SetViewport(&vp);
        dev->SetVertexShader(s_vs);
        dev->SetPixelShader(s_psGain);
        dev->SetVertexDeclaration(s_decl);
        IDirect3DBaseTexture9* tex[3] = { scene, ave, bloom };
        for (DWORD i = 0; i < 3; i++) {
            dev->SetTexture(i, tex[i]);
            dev->SetSamplerState(i, D3DSAMP_MINFILTER, D3DTEXF_LINEAR);
            dev->SetSamplerState(i, D3DSAMP_MAGFILTER, D3DTEXF_LINEAR);
            dev->SetSamplerState(i, D3DSAMP_MIPFILTER, D3DTEXF_NONE);
            dev->SetSamplerState(i, D3DSAMP_ADDRESSU, D3DTADDRESS_CLAMP);
            dev->SetSamplerState(i, D3DSAMP_ADDRESSV, D3DTADDRESS_CLAMP);
            dev->SetSamplerState(i, D3DSAMP_SRGBTEXTURE, FALSE);
        }
        dev->SetRenderState(D3DRS_ZENABLE, FALSE);
        dev->SetRenderState(D3DRS_ZWRITEENABLE, FALSE);
        dev->SetRenderState(D3DRS_ALPHABLENDENABLE, FALSE);
        dev->SetRenderState(D3DRS_ALPHATESTENABLE, FALSE);
        dev->SetRenderState(D3DRS_STENCILENABLE, FALSE);
        dev->SetRenderState(D3DRS_SCISSORTESTENABLE, FALSE);
        dev->SetRenderState(D3DRS_CULLMODE, D3DCULL_NONE);
        dev->SetRenderState(D3DRS_SRGBWRITEENABLE, FALSE);
        dev->SetRenderState(D3DRS_COLORWRITEENABLE, 0xF);
        dev->SetRenderState(D3DRS_FILLMODE, D3DFILL_SOLID);
        float halfPixel[4] = { -1.0f / vp.Width, 1.0f / vp.Height, 0, 0 };
        dev->SetVertexShaderConstantF(0, halfPixel, 1);
        sz[0] = 1.0f / sd.Width;
        sz[1] = 1.0f / sd.Height;
        dev->SetPixelShaderConstantF(0, fc, 1);
        dev->SetPixelShaderConstantF(1, gc, 1);
        dev->SetPixelShaderConstantF(2, hc, 1);
        dev->SetPixelShaderConstantF(3, sz, 1);
        static const float quad[4][6] = {
            { -1, 1, 0, 1, 0, 0 }, { 1, 1, 0, 1, 1, 0 }, { -1, -1, 0, 1, 0, 1 }, { 1, -1, 0, 1, 1, 1 },
        };
        dev->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, quad, sizeof(quad[0]));  // the game's scene is open
        s_state->Apply();
        RestoreStage0Tracking();
        dev->SetRenderTarget(0, rt0);
        dev->SetDepthStencilSurface(ds);
        dev->SetViewport(&vp);
        dev->SetScissorRect(&scissor);
        s_toneDone = true;
        s_snapDone = s_movieSeen = false;
        if (g_cfg.log) {
            static unsigned calls;
            if (calls++ % 600 == 0)
                Log("HDR: gain pass: exposure %.3f %.3f, curve p %.3f q %.3f k %.3f, knee 2^%.2f, slope %.2f, %.2f stops to "
                    "the peak, bloom curve %.2f, viewport %lu,%lu %lux%lu of %ux%u, %u passes in %u frames, HUD copies %u",
                    fc[0], fc[1], fc[2], fc[3], gc[0], hc[0], hc[2] * hc[3], hc[3], gc[1], vp.X, vp.Y, vp.Width, vp.Height,
                    rd.Width, rd.Height, s_toneCalls, s_presents, s_snaps);
        }
    } else if (g_cfg.log) {
        static int logged;
        if (logged++ < 3)
            Log("HDR: gain pass skipped (scene format %d, %ux%u, peak/paper white %.2f)", sd.Format, sd.Width, sd.Height,
                PeakNits() / g_cfg.hdrPaperWhite);
    }
    if (scene) scene->Release();
    if (ave) ave->Release();
    if (bloom) bloom->Release();
    if (rt0) rt0->Release();
    if (ds) ds->Release();
}

// Scaleform's GFxMovieRoot::Display (vtable 0xC41CF4 slot 31): at the first movie after the tone mapper the
// game's target holds the finished picture without the HUD. Only the vtable slot is swapped; UltrawideFix=2
// may have put its own hook there before (it calls the game's function), which is called on. The addresses
// go through G() when the hook is installed, not at load time.
typedef void(__thiscall* GfxDisplay_t)(void* self);
static GfxDisplay_t s_origGfxDisplay;

static void __fastcall Hook_GfxDisplay(void* self, void*)
{
    if (g_cfg.smaa) SmaaBeforeHud();  // before the HUD copy, so that the copy holds the smoothed picture
    if (s_toneDone && !s_snapDone && s_snapSurf && s_dev) {
        s_movieSeen = true;
        IDirect3DSurface9* rt = NULL;
        s_dev->GetRenderTarget(0, &rt);
        D3DSURFACE_DESC d;
        if (rt && SUCCEEDED(rt->GetDesc(&d)) && d.Width == s_gainW && d.Height == s_gainH &&
            SUCCEEDED(s_dev->StretchRect(rt, NULL, s_snapSurf, NULL, D3DTEXF_POINT))) {
            s_snapDone = true;
            s_snaps++;
        }
        if (rt) rt->Release();
    }
    s_origGfxDisplay(self);
}

static bool OwnCode(const void* p)
{
    const DWORD flags = GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT;
    HMODULE m = NULL, self = NULL;
    return GetModuleHandleExA(flags, (LPCSTR)p, &m) && GetModuleHandleExA(flags, (LPCSTR)&OwnCode, &self) && m == self;
}

bool HdrUiHooked() { return s_uiHooked; }

// after UltrawideDeviceCreated, so that its check of the slot sees the game's function. Used by HDR (the
// HUD copy) and SMAA (smoothing before the HUD).
void HdrInstallUiHook()
{
    if (((!g_cfg.hdr || s_failed) && !g_cfg.smaa) || s_uiHooked) return;
    if (CurrentBuild() == BUILD_UNKNOWN) {
        Log("the HUD is told apart on the known Steam builds only; here no HDR light above white and no SMAA");
        return;
    }
    void** const kGfxDisplaySlot = (void**)G(0x00C41CF4) + 31;
    void* cur = IsBadReadPtr(kGfxDisplaySlot, 4) ? NULL : *kGfxDisplaySlot;
    if (!cur || (cur != (void*)G(0x00987E60) && !OwnCode(cur))) {
        Log("Scaleform movie class not recognised, the HUD is not told apart (no HDR light above white, no SMAA)");
        return;
    }
    s_origGfxDisplay = (GfxDisplay_t)cur;
    void* hook = (void*)Hook_GfxDisplay;
    WriteMemory(kGfxDisplaySlot, &hook, sizeof(hook));
    s_uiHooked = true;
}

// [DLC] Pickups (dlcpickups.cpp): this frame's gain target, right after the tone mapping (NULL when there is none),
// so that what the fix draws into the tone mapped picture can write gain 1 there and stay at paper white like the HUD
IDirect3DSurface9* HdrGainTarget() { return g_cfg.hdr && !s_failed && s_toneDone ? s_gainSurf : NULL; }

#ifdef HDR_TEST  // the offline check (scratch test program) reads the swap chain back
ID3D11Device* HdrTestDevice11() { return s_dev11; }
IDXGISwapChain1* HdrTestSwap() { return s_swap; }
IDirect3DSurface9* HdrTestSys() { return s_sys[1]; }
IDirect3DSurface9* HdrTestGain() { return s_gainSurf; }
static void __fastcall TestNoDisplay(void*, void*) {}
void HdrTestDisplay()
{
    s_uiHooked = true;
    s_origGfxDisplay = (GfxDisplay_t)TestNoDisplay;
    Hook_GfxDisplay(NULL, NULL);
}
#endif
