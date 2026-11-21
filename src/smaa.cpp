// SMAA 1x (Jimenez et al., third_party/smaa, MIT license) on the finished 3D picture, before the HUD.
//
// The game draws its HUD and menus with Scaleform after the post processing chain. hdr.cpp's hook of
// GFxMovieRoot::Display calls SmaaBeforeHud at the first movie of a frame in which the tone mapper ran
// (postfx.cpp's bloom hook calls SmaaSceneDone) whose target has the tone mapper's size: that target
// then holds the 3D picture only, so edges are smoothed there and the HUD's text stays sharp. Movies
// drawn into textures of other sizes (menu panels, split screen dials) are passed over. Only the tone
// mapper's viewport is written (a scissor rect; the viewports of tone mappings into the same target
// without a movie between them, both split screen halves, are united), so no HUD drawn before is
// touched. A picture that no movie followed (photo mode without HUD) is smoothed on the back buffer at
// Present instead. Without the Scaleform hook nothing is done.
//
// The three passes of the reference: luma edge detection, blending weights (with its area and search
// tables) and neighbourhood blending back into the game's target. Only our own render targets and
// shaders are used, the device state is captured and put back; no game code is changed.
#include "common.h"
#include <d3d9.h>
#include "../third_party/smaa/AreaTex.h"
#include "../third_party/smaa/SearchTex.h"
#include "smaa_EdgeVS.h"  // build\, compiled from src\smaa.hlsl by build.bat
#include "smaa_EdgePS.h"
#include "smaa_WeightVS.h"
#include "smaa_WeightPS.h"
#include "smaa_BlendVS.h"
#include "smaa_BlendPS.h"

template <class T> static void Drop(T*& p)
{
    if (p) p->Release();
    p = NULL;
}

static IDirect3DDevice9* s_dev;
static IDirect3DVertexShader9 *s_vsEdge, *s_vsWeight, *s_vsBlend;
static IDirect3DPixelShader9 *s_psEdge, *s_psWeight, *s_psBlend;
static IDirect3DVertexDeclaration9* s_decl;
static IDirect3DTexture9 *s_area, *s_search;            // managed: survive a Reset
static IDirect3DTexture9 *s_color, *s_edges, *s_blend;  // default pool, the picture's size
static IDirect3DSurface9 *s_colorSurf, *s_edgesSurf, *s_blendSurf;
static IDirect3DStateBlock9* s_state;
static UINT s_w, s_h;
static bool s_failed, s_srgbRead;
static int s_targetFailures;
static bool s_pending;          // the tone mapper ran, its picture is not smoothed yet
static UINT s_sceneW, s_sceneH;  // the tone mapper's target
static D3DVIEWPORT9 s_sceneVp;   // and its viewport
static IDirect3DSurface9* s_sceneRt;  // the tone mapper's target, compared only (no reference held)
static D3DFORMAT s_srgbFormat[2];  // targets checked for sRGB writes: [0] can, [1] cannot

static void Fail(const char* why)
{
    Log("SMAA: %s, anti-aliasing off", why);
    s_failed = true;
}

static bool MakeTables(IDirect3DDevice9* dev)
{
    D3DLOCKED_RECT r;
    if (FAILED(dev->CreateTexture(AREATEX_WIDTH, AREATEX_HEIGHT, 1, 0, D3DFMT_A8L8, D3DPOOL_MANAGED, &s_area, NULL)) ||
        FAILED(s_area->LockRect(0, &r, NULL, 0)))
        return false;
    for (UINT y = 0; y < AREATEX_HEIGHT; y++)  // R8G8 rows: L = R, A = G (read as .ra)
        memcpy((BYTE*)r.pBits + y * r.Pitch, areaTexBytes + y * AREATEX_PITCH, AREATEX_PITCH);
    s_area->UnlockRect(0);
    if (FAILED(dev->CreateTexture(SEARCHTEX_WIDTH, SEARCHTEX_HEIGHT, 1, 0, D3DFMT_L8, D3DPOOL_MANAGED, &s_search, NULL)) ||
        FAILED(s_search->LockRect(0, &r, NULL, 0)))
        return false;
    for (UINT y = 0; y < SEARCHTEX_HEIGHT; y++)
        memcpy((BYTE*)r.pBits + y * r.Pitch, searchTexBytes + y * SEARCHTEX_PITCH, SEARCHTEX_PITCH);
    s_search->UnlockRect(0);
    return true;
}

static bool FormatOk(DWORD usage, D3DRESOURCETYPE type, D3DFORMAT format)
{
    IDirect3D9* d3d = NULL;
    D3DDEVICE_CREATION_PARAMETERS cp = {};
    bool ok = SUCCEEDED(s_dev->GetDirect3D(&d3d)) && d3d && SUCCEEDED(s_dev->GetCreationParameters(&cp)) &&
              SUCCEEDED(d3d->CheckDeviceFormat(cp.AdapterOrdinal, D3DDEVTYPE_HAL, D3DFMT_X8R8G8B8, usage, type, format));
    if (d3d) d3d->Release();
    return ok;
}

// sRGB writes into the picture's surface, asked once per format
static bool SrgbWrite(D3DFORMAT f)
{
    if (s_srgbFormat[0] == f) return true;
    if (s_srgbFormat[1] == f) return false;
    bool ok = FormatOk(D3DUSAGE_RENDERTARGET | D3DUSAGE_QUERY_SRGBWRITE, D3DRTYPE_SURFACE, f);
    s_srgbFormat[ok ? 0 : 1] = f;
    if (g_cfg.log) Log("SMAA: blending into format %d %s", f, ok ? "in linear light" : "in gamma space");
    return ok;
}

void SmaaDeviceCreated(IDirect3DDevice9* dev)
{
    if (!g_cfg.smaa || s_failed || !dev || s_dev) return;
    s_dev = dev;
    static const D3DVERTEXELEMENT9 decl[] = {
        { 0, 0, D3DDECLTYPE_FLOAT4, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_POSITION, 0 },
        { 0, 16, D3DDECLTYPE_FLOAT2, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_TEXCOORD, 0 },
        D3DDECL_END(),
    };
    if (FAILED(dev->CreateVertexShader((const DWORD*)g_smaaEdgeVS, &s_vsEdge)) ||
        FAILED(dev->CreateVertexShader((const DWORD*)g_smaaWeightVS, &s_vsWeight)) ||
        FAILED(dev->CreateVertexShader((const DWORD*)g_smaaBlendVS, &s_vsBlend)) ||
        FAILED(dev->CreatePixelShader((const DWORD*)g_smaaEdgePS, &s_psEdge)) ||
        FAILED(dev->CreatePixelShader((const DWORD*)g_smaaWeightPS, &s_psWeight)) ||
        FAILED(dev->CreatePixelShader((const DWORD*)g_smaaBlendPS, &s_psBlend)) ||
        FAILED(dev->CreateVertexDeclaration(decl, &s_decl)))
        return Fail("shaders not created");
    if (!MakeTables(dev)) return Fail("tables not created");
    // the blend into the picture in linear light, as the reference does, where the card can (the write
    // side is checked per target format in Run)
    s_srgbRead = FormatOk(D3DUSAGE_QUERY_SRGBREAD, D3DRTYPE_TEXTURE, D3DFMT_A8R8G8B8);
    Log("SMAA 1x on");
}

static void DropTargets()
{
    Drop(s_colorSurf);
    Drop(s_edgesSurf);
    Drop(s_blendSurf);
    Drop(s_color);
    Drop(s_edges);
    Drop(s_blend);
    Drop(s_state);
    s_w = s_h = 0;
}

void SmaaDeviceLost()
{
    DropTargets();
    s_pending = false;
    s_sceneRt = NULL;
}

static bool MakeTargets(UINT w, UINT h)
{
    if (w == s_w && h == s_h && s_state) return true;
    DropTargets();
    IDirect3DDevice9* dev = s_dev;
    IDirect3DTexture9** tex[3] = { &s_color, &s_edges, &s_blend };
    IDirect3DSurface9** surf[3] = { &s_colorSurf, &s_edgesSurf, &s_blendSurf };
    HRESULT hr = S_OK;
    for (int i = 0; i < 3 && SUCCEEDED(hr); i++) {
        hr = dev->CreateTexture(w, h, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, tex[i], NULL);
        if (SUCCEEDED(hr)) hr = (*tex[i])->GetSurfaceLevel(0, surf[i]);
    }
    if (SUCCEEDED(hr)) hr = dev->CreateStateBlock(D3DSBT_ALL, &s_state);
    if (FAILED(hr)) {
        Log("SMAA: targets %ux%u failed (%08lx)", w, h, (unsigned long)hr);
        DropTargets();
        if (++s_targetFailures >= 3) Fail("no video memory for its targets");
        return false;
    }
    s_targetFailures = 0;
    s_w = w;
    s_h = h;
    return true;
}

static void Sampler(IDirect3DDevice9* dev, DWORD i, IDirect3DBaseTexture9* tex, D3DTEXTUREFILTERTYPE filter, BOOL srgb)
{
    dev->SetTexture(i, tex);
    dev->SetSamplerState(i, D3DSAMP_MINFILTER, filter);
    dev->SetSamplerState(i, D3DSAMP_MAGFILTER, filter);
    dev->SetSamplerState(i, D3DSAMP_MIPFILTER, D3DTEXF_NONE);
    dev->SetSamplerState(i, D3DSAMP_ADDRESSU, D3DTADDRESS_CLAMP);
    dev->SetSamplerState(i, D3DSAMP_ADDRESSV, D3DTADDRESS_CLAMP);
    dev->SetSamplerState(i, D3DSAMP_SRGBTEXTURE, srgb);
    dev->SetSamplerState(i, D3DSAMP_MAXMIPLEVEL, 0);
}

static void Pass(IDirect3DDevice9* dev, IDirect3DVertexShader9* vs, IDirect3DPixelShader9* ps)
{
    static const float quad[4][6] = {
        { -1, 1, 0, 1, 0, 0 }, { 1, 1, 0, 1, 1, 0 }, { -1, -1, 0, 1, 0, 1 }, { 1, -1, 0, 1, 1, 1 },
    };
    dev->SetVertexShader(vs);
    dev->SetPixelShader(ps);
    dev->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, quad, sizeof(quad[0]));
}

// smooths the picture in target (the game's render target, or the back buffer) in place, within vp
static void Run(IDirect3DDevice9* dev, IDirect3DSurface9* target, bool openScene, const D3DVIEWPORT9* only)
{
    D3DSURFACE_DESC d = {};
    if (FAILED(target->GetDesc(&d)) || d.MultiSampleType != D3DMULTISAMPLE_NONE ||
        (d.Format != D3DFMT_A8R8G8B8 && d.Format != D3DFMT_X8R8G8B8)) {
        static bool logged;
        if (!logged) Log("SMAA: picture format %d not handled", d.Format);
        logged = true;
        return;
    }
    if (!MakeTargets(d.Width, d.Height)) return;
    if (FAILED(dev->StretchRect(target, NULL, s_colorSurf, NULL, D3DTEXF_POINT))) return;

    IDirect3DSurface9 *rt[4] = {}, *ds = NULL;
    for (DWORD i = 0; i < 4; i++) dev->GetRenderTarget(i, &rt[i]);
    dev->GetDepthStencilSurface(&ds);
    D3DVIEWPORT9 vp;
    RECT scissor;
    dev->GetViewport(&vp);
    dev->GetScissorRect(&scissor);
    s_state->Capture();
    SaveStage0Tracking();
    bool scene = openScene || SUCCEEDED(dev->BeginScene());

    for (DWORD i = 1; i < 4; i++) dev->SetRenderTarget(i, NULL);
    dev->SetDepthStencilSurface(NULL);
    dev->SetVertexDeclaration(s_decl);
    dev->SetRenderState(D3DRS_ZENABLE, FALSE);
    dev->SetRenderState(D3DRS_ZWRITEENABLE, FALSE);
    dev->SetRenderState(D3DRS_ALPHABLENDENABLE, FALSE);
    dev->SetRenderState(D3DRS_ALPHATESTENABLE, FALSE);
    dev->SetRenderState(D3DRS_STENCILENABLE, FALSE);
    dev->SetRenderState(D3DRS_SCISSORTESTENABLE, FALSE);
    dev->SetRenderState(D3DRS_CULLMODE, D3DCULL_NONE);
    dev->SetRenderState(D3DRS_FILLMODE, D3DFILL_SOLID);
    dev->SetRenderState(D3DRS_FOGENABLE, FALSE);
    dev->SetRenderState(D3DRS_CLIPPLANEENABLE, 0);
    dev->SetRenderState(D3DRS_COLORWRITEENABLE, 0xF);
    dev->SetRenderState(D3DRS_SRGBWRITEENABLE, FALSE);
    const float metrics[4] = { 1.0f / d.Width, 1.0f / d.Height, (float)d.Width, (float)d.Height };
    dev->SetVertexShaderConstantF(0, metrics, 1);
    dev->SetPixelShaderConstantF(0, metrics, 1);

    // 1: edges of the picture's luma
    dev->SetRenderTarget(0, s_edgesSurf);
    dev->Clear(0, NULL, D3DCLEAR_TARGET, 0, 1.0f, 0);
    Sampler(dev, 0, s_color, D3DTEXF_LINEAR, FALSE);
    Pass(dev, s_vsEdge, s_psEdge);
    // 2: blending weights along the edges
    dev->SetRenderTarget(0, s_blendSurf);
    dev->Clear(0, NULL, D3DCLEAR_TARGET, 0, 1.0f, 0);
    Sampler(dev, 1, s_edges, D3DTEXF_LINEAR, FALSE);
    Sampler(dev, 2, s_area, D3DTEXF_LINEAR, FALSE);
    Sampler(dev, 3, s_search, D3DTEXF_POINT, FALSE);
    Pass(dev, s_vsWeight, s_psWeight);
    // 3: the picture blended with its neighbours, back into the target, only where the picture is. The
    // quad keeps the whole target (SetRenderTarget set the viewport to it), so every pixel reads its own
    // texel; a scissor rect keeps the writes inside the picture. A viewport would squeeze the whole
    // picture into it: in split screen both halves into one half, three pictures on screen (issue #15).
    dev->SetRenderTarget(0, target);
    if (only) {
        RECT r = { (LONG)only->X, (LONG)only->Y, (LONG)(only->X + only->Width), (LONG)(only->Y + only->Height) };
        if (r.right > (LONG)d.Width) r.right = (LONG)d.Width;
        if (r.bottom > (LONG)d.Height) r.bottom = (LONG)d.Height;
        if (r.left > 0 || r.top > 0 || r.right < (LONG)d.Width || r.bottom < (LONG)d.Height) {
            static int logged;
            if (g_cfg.log && logged++ < 3)
                Log("SMAA: picture in %ld,%ld-%ld,%ld of %ux%u, written there only", r.left, r.top, r.right, r.bottom,
                    d.Width, d.Height);
            dev->SetScissorRect(&r);
            dev->SetRenderState(D3DRS_SCISSORTESTENABLE, TRUE);
        }
    }
    const BOOL srgb = s_srgbRead && SrgbWrite(d.Format);
    Sampler(dev, 0, s_color, D3DTEXF_LINEAR, srgb);
    Sampler(dev, 4, s_blend, D3DTEXF_LINEAR, FALSE);
    dev->SetRenderState(D3DRS_SRGBWRITEENABLE, srgb);
    Pass(dev, s_vsBlend, s_psBlend);

    if (scene && !openScene) dev->EndScene();
    s_state->Apply();
    RestoreStage0Tracking();
    for (DWORD i = 0; i < 4; i++) {
        if (i == 0 || rt[i]) dev->SetRenderTarget(i, rt[i]);
        if (rt[i]) rt[i]->Release();
    }
    dev->SetDepthStencilSurface(ds);
    if (ds) ds->Release();
    dev->SetViewport(&vp);
    dev->SetScissorRect(&scissor);
}

static bool TargetSize(IDirect3DSurface9* s, UINT* w, UINT* h)
{
    D3DSURFACE_DESC d;
    if (!s || FAILED(s->GetDesc(&d))) return false;
    *w = d.Width;
    *h = d.Height;
    return true;
}

// postfx.cpp's bloom hook, right after the tone mapper: its target and viewport
void SmaaSceneDone()
{
    if (!s_dev || s_failed || !HdrUiHooked()) return;  // without the Scaleform hook the HUD would be smoothed too
    IDirect3DSurface9* rt = NULL;
    s_dev->GetRenderTarget(0, &rt);
    UINT w = 0, h = 0;
    D3DVIEWPORT9 vp = {};
    bool ok = TargetSize(rt, &w, &h) && SUCCEEDED(s_dev->GetViewport(&vp));
    const bool same = rt == s_sceneRt;  // compared only, before the release
    if (rt) rt->Release();
    if (ok && s_pending && same && w == s_sceneW && h == s_sceneH) {
        // split screen: the other player's tone mapping before any movie; both halves are 3D picture
        static int logged;
        if (g_cfg.log && logged++ < 3)
            Log("SMAA: tone mapping %lu,%lu %lux%lu united with %lu,%lu %lux%lu", vp.X, vp.Y, vp.Width, vp.Height,
                s_sceneVp.X, s_sceneVp.Y, s_sceneVp.Width, s_sceneVp.Height);
        DWORD x0 = min(vp.X, s_sceneVp.X), y0 = min(vp.Y, s_sceneVp.Y);
        DWORD x1 = max(vp.X + vp.Width, s_sceneVp.X + s_sceneVp.Width);
        DWORD y1 = max(vp.Y + vp.Height, s_sceneVp.Y + s_sceneVp.Height);
        s_sceneVp.X = x0;
        s_sceneVp.Y = y0;
        s_sceneVp.Width = x1 - x0;
        s_sceneVp.Height = y1 - y0;
        return;
    }
    s_pending = ok;
    s_sceneRt = ok ? rt : NULL;
    if (ok) {
        s_sceneW = w;
        s_sceneH = h;
        s_sceneVp = vp;
    }
}

// hdr.cpp's Scaleform hook, at every movie drawn: the first one after the 3D picture into a target of the
// tone mapper's size
void SmaaBeforeHud()
{
    if (!s_pending || !s_dev) return;
    IDirect3DSurface9* rt = NULL;
    UINT w, h;
    if (SUCCEEDED(s_dev->GetRenderTarget(0, &rt)) && TargetSize(rt, &w, &h) && w == s_sceneW && h == s_sceneH) {
        s_pending = false;
        Run(s_dev, rt, true, &s_sceneVp);
    }
    if (rt) rt->Release();
}

// diag.cpp's Present hook, before anything else touches the back buffer
void SmaaPresent(IDirect3DDevice9* dev)
{
    if (s_pending && dev == s_dev) {  // no movie after the picture: the back buffer is the picture
        IDirect3DSurface9* bb = NULL;
        UINT w, h;
        if (SUCCEEDED(dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb)) && TargetSize(bb, &w, &h))
            Run(dev, bb, false, w == s_sceneW && h == s_sceneH ? &s_sceneVp : NULL);
        if (bb) bb->Release();
    }
    s_pending = false;
}
