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
// Stage 1 (this): the picture as the game makes it, in an HDR10 signal with an adjustable paper white.
// With Log=1 the tone mapping pass is described in the log and its pixel shader saved, for stage 2
// (the light above white from the game's own float image).
#include "common.h"
#include <d3d9.h>
#include <d3d11.h>
#include <dxgi1_6.h>
#include <d3dcompiler.h>
#include <stdio.h>

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
    "float4 k : register(c0);  // x paper white / 10000, z gamma of the game's picture\n"
    "float3 PQ(float3 y) {\n"
    "    y = pow(max(y, 0.0), 0.1593017578125);\n"
    "    return pow((0.8359375 + 18.8515625 * y) / (1.0 + 18.6875 * y), 78.84375);\n"
    "}\n"
    "float4 ps(float2 t : TEXCOORD0) : COLOR {\n"
    "    float3 c = pow(max(tex2D(scene, t).rgb, 0.0), k.z);\n"
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

static bool MakeShaders(IDirect3DDevice9* dev)
{
    if (s_ps) return true;
    HMODULE comp = LoadLibraryA("d3dcompiler_47.dll");
    D3DCompile_t compile = comp ? (D3DCompile_t)GetProcAddress(comp, "D3DCompile") : NULL;
    if (!compile) {
        Log("HDR: d3dcompiler_47.dll not found");
        return false;
    }
    D3D_SHADER_MACRO defs[] = { { "SWAP_RB", s_pqFormat == D3DFMT_A2R10G10B10 ? "1" : "0" }, { NULL, NULL } };
    ID3DBlob *vsCode = NULL, *psCode = NULL, *err = NULL;
    HRESULT hr = compile(kShader, sizeof(kShader) - 1, "hdr", defs, NULL, "vs", "vs_3_0", 0, 0, &vsCode, &err);
    if (SUCCEEDED(hr)) hr = compile(kShader, sizeof(kShader) - 1, "hdr", defs, NULL, "ps", "ps_3_0", 0, 0, &psCode, &err);
    if (FAILED(hr)) {
        Log("HDR: shader: %s", err ? (const char*)err->GetBufferPointer() : "failed");
        Drop(err);
        Drop(vsCode);
        return false;
    }
    dev->CreateVertexShader((const DWORD*)vsCode->GetBufferPointer(), &s_vs);
    dev->CreatePixelShader((const DWORD*)psCode->GetBufferPointer(), &s_ps);
    Drop(vsCode);
    Drop(psCode);
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

static void DropTargets()
{
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
    dev->SetVertexShader(s_vs);
    dev->SetPixelShader(s_ps);
    dev->SetVertexDeclaration(s_decl);
    dev->SetTexture(0, s_sceneTex);
    dev->SetSamplerState(0, D3DSAMP_MINFILTER, D3DTEXF_POINT);
    dev->SetSamplerState(0, D3DSAMP_MAGFILTER, D3DTEXF_POINT);
    dev->SetSamplerState(0, D3DSAMP_MIPFILTER, D3DTEXF_NONE);
    dev->SetSamplerState(0, D3DSAMP_ADDRESSU, D3DTADDRESS_CLAMP);
    dev->SetSamplerState(0, D3DSAMP_ADDRESSV, D3DTADDRESS_CLAMP);
    dev->SetSamplerState(0, D3DSAMP_SRGBTEXTURE, FALSE);
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
    float k[4] = { g_cfg.hdrPaperWhite / 10000.0f, PeakNits() / 10000.0f, 2.2f, 0 };
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

// ------------------------------------------------------------------ the tone mapping pass (Log=1, for stage 2)

void HdrNoteToneMap()
{
    static int noted;
    if (!g_cfg.log || !s_dev || noted >= 2) return;
    noted++;
    IDirect3DDevice9* dev = s_dev;
    IDirect3DPixelShader9* ps = NULL;
    dev->GetPixelShader(&ps);
    if (ps) {
        UINT size = 0;
        ps->GetFunction(NULL, &size);
        if (size && size < (1u << 20)) {
            BYTE* code = (BYTE*)malloc(size);
            if (code && SUCCEEDED(ps->GetFunction(code, &size))) {
                char path[MAX_PATH], name[64];
                sprintf(name, "glimpse_tonemap_ps%d.bin", noted);
                ModulePath(path, name);
                HANDLE f = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
                DWORD w;
                if (f != INVALID_HANDLE_VALUE) {
                    WriteFile(f, code, size, &w, NULL);
                    CloseHandle(f);
                }
                Log("HDR: tone mapping pixel shader %u bytes saved as %s", size, name);
            }
            free(code);
        }
        ps->Release();
    }
    for (DWORD s = 0; s < 8; s++) {
        IDirect3DBaseTexture9* t = NULL;
        dev->GetTexture(s, &t);
        if (!t) continue;
        if (t->GetType() == D3DRTYPE_TEXTURE) {
            D3DSURFACE_DESC d;
            ((IDirect3DTexture9*)t)->GetLevelDesc(0, &d);
            Log("HDR: tone mapping stage %lu: %ux%u format %d usage %lx", s, d.Width, d.Height, d.Format, d.Usage);
        }
        t->Release();
    }
    float c[32][4];
    if (SUCCEEDED(dev->GetPixelShaderConstantF(0, &c[0][0], 32)))
        for (int i = 0; i < 32; i++)
            if (c[i][0] || c[i][1] || c[i][2] || c[i][3])
                Log("HDR: tone mapping c%d = %.4f %.4f %.4f %.4f", i, c[i][0], c[i][1], c[i][2], c[i][3]);
    IDirect3DSurface9* rt = NULL;
    dev->GetRenderTarget(0, &rt);
    if (rt) {
        D3DSURFACE_DESC d;
        rt->GetDesc(&d);
        Log("HDR: tone mapping target %ux%u format %d", d.Width, d.Height, d.Format);
        rt->Release();
    }
}

#ifdef HDR_TEST  // the offline check (scratch test program) reads the swap chain back
ID3D11Device* HdrTestDevice11() { return s_dev11; }
IDXGISwapChain1* HdrTestSwap() { return s_swap; }
IDirect3DSurface9* HdrTestSys() { return s_sys[1]; }
#endif
