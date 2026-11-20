// Split/Second PC fix - Direct3D 9 hooks: device creation log and back buffer capture (Log=1),
// forced anisotropic filtering (Anisotropy=2..16), 60 fps pacing after Present (framerate.cpp)
#include "common.h"
#include <d3d9.h>
#include <intrin.h>
#include <stdio.h>
#include <string.h>

typedef IDirect3D9*(WINAPI* Direct3DCreate9_t)(UINT);
typedef HRESULT(STDMETHODCALLTYPE* CreateDevice_t)(IDirect3D9*, UINT, D3DDEVTYPE, HWND, DWORD, D3DPRESENT_PARAMETERS*, IDirect3DDevice9**);

typedef HRESULT(STDMETHODCALLTYPE* Present_t)(IDirect3DDevice9*, const RECT*, const RECT*, HWND, const RGNDATA*);
typedef HRESULT(WINAPI* D3DXSaveSurfaceToFileA_t)(LPCSTR, DWORD, IDirect3DSurface9*, const PALETTEENTRY*, const RECT*);

static Direct3DCreate9_t s_origCreate9;
static CreateDevice_t s_origCreateDevice;
static Present_t s_origPresent;
static BYTE s_create9Prologue[5];

// ---------------------------------------------------------------- anisotropic filtering

// MINFILTER LINEAR becomes ANISOTROPIC on pixel samplers that hold a mipmapped texture which is not
// a render target or depth texture (shadow maps rely on plain linear filtering for PCF).
// TextureDetail=1..3: on the same samplers MAXMIPLEVEL skips that many of the texture's top levels (half,
// a quarter, an eighth of the size); textures without mip levels (text, HUD, menus) stay as they are.
typedef HRESULT(STDMETHODCALLTYPE* SetTexture_t)(IDirect3DDevice9*, DWORD, IDirect3DBaseTexture9*);
typedef HRESULT(STDMETHODCALLTYPE* SetSamplerState_t)(IDirect3DDevice9*, DWORD, D3DSAMPLERSTATETYPE, DWORD);
static SetTexture_t s_origSetTexture;
static SetSamplerState_t s_origSetSamplerState;

struct StageState {
    IDirect3DBaseTexture9* tex;
    bool eligible;
    DWORD minFilter;  // as the game set it
    DWORD maxMip;     // MAXMIPLEVEL as the game set it
};
static StageState s_stages[16];
// Log=1: calls that reached the hooks, logged every 10 s with the device's vtable (the F9 dump of the
// 2.1.0 tests counted no draws, and the shadow survey saw only a few of the game's render targets)
static volatile LONG s_drawsSeen, s_setTextureSeen, s_createSeen;

static bool Eligible(IDirect3DBaseTexture9* t)
{
    if (!t || t->GetLevelCount() < 2) return false;
    D3DSURFACE_DESC d;
    D3DRESOURCETYPE type = t->GetType();
    if (type == D3DRTYPE_TEXTURE) {
        if (FAILED(((IDirect3DTexture9*)t)->GetLevelDesc(0, &d))) return false;
    } else if (type == D3DRTYPE_CUBETEXTURE) {
        if (FAILED(((IDirect3DCubeTexture9*)t)->GetLevelDesc(0, &d))) return false;
    } else {
        return false;
    }
    return !(d.Usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL));
}

static void ApplyFilter(IDirect3DDevice9* dev, DWORD stage)
{
    StageState& s = s_stages[stage];
    bool aniso = g_cfg.anisotropy > 1 && s.eligible && s.minFilter == D3DTEXF_LINEAR;
    s_origSetSamplerState(dev, stage, D3DSAMP_MINFILTER, aniso ? D3DTEXF_ANISOTROPIC : s.minFilter);
    if (aniso) s_origSetSamplerState(dev, stage, D3DSAMP_MAXANISOTROPY, (DWORD)g_cfg.anisotropy);
}

static void ApplyMipLimit(IDirect3DDevice9* dev, DWORD stage)
{
    StageState& s = s_stages[stage];
    DWORD level = s.maxMip;
    if (s.eligible && level < (DWORD)g_cfg.textureDetail) level = (DWORD)g_cfg.textureDetail;
    s_origSetSamplerState(dev, stage, D3DSAMP_MAXMIPLEVEL, level);
}

static HRESULT STDMETHODCALLTYPE Hook_SetTexture(IDirect3DDevice9* dev, DWORD stage, IDirect3DBaseTexture9* tex)
{
    HRESULT hr = s_origSetTexture(dev, stage, tex);
    s_setTextureSeen++;
    if (stage < 16 && tex != s_stages[stage].tex) {
        s_stages[stage].tex = tex;
        bool eligible = Eligible(tex);
        if (eligible != s_stages[stage].eligible) {
            s_stages[stage].eligible = eligible;
            if (s_stages[stage].minFilter == D3DTEXF_LINEAR) ApplyFilter(dev, stage);
            if (g_cfg.textureDetail > 0) ApplyMipLimit(dev, stage);
        }
    }
    return hr;
}

// A pass of our own (HDR, SMAA, the overlay) sets samplers through the device and then puts the game's
// state back with a state block, past these hooks: what they noted of every stage is kept around it.
static StageState s_savedStages[16];
void SaveStage0Tracking() { memcpy(s_savedStages, s_stages, sizeof(s_stages)); }
void RestoreStage0Tracking() { memcpy(s_stages, s_savedStages, sizeof(s_stages)); }

// A Reset puts every sampler and texture stage back to the device's defaults; what the hooks noted is
// forgotten with it, so the next texture and filter the game sets are taken up afresh (the same managed
// texture bound again after the Reset would otherwise keep a stage at full detail).
static void ForgetStages()
{
    for (StageState& s : s_stages) {
        s.tex = NULL;
        s.eligible = false;
        s.minFilter = D3DTEXF_POINT;
        s.maxMip = 0;
    }
}

// The motion blur pass samples along the motion; at speed (and more so with the doubled blur at 60 fps
// and the boost) the samples near the bottom edge reach past the picture and bring in a thin line from
// outside it. While the pass runs, U and V addressing of the first stages is clamped to the edge. What
// the game sets meanwhile is kept and set when the pass ends, so the device ends up in the state the game
// expects (it skips settings it believes are already there).
static IDirect3DDevice9* s_device;
static bool s_clamp;
static DWORD s_wantAddress[8][2];  // U, V as the game wants them

void BeginEdgeClamp()
{
    if (!s_device || !s_origSetSamplerState || s_clamp) return;
    for (DWORD stage = 0; stage < 8; stage++)
        for (int i = 0; i < 2; i++) {
            D3DSAMPLERSTATETYPE type = i ? D3DSAMP_ADDRESSV : D3DSAMP_ADDRESSU;
            if (FAILED(s_device->GetSamplerState(stage, type, &s_wantAddress[stage][i]))) s_wantAddress[stage][i] = D3DTADDRESS_WRAP;
            s_origSetSamplerState(s_device, stage, type, D3DTADDRESS_CLAMP);
        }
    s_clamp = true;
}

void EndEdgeClamp()
{
    if (!s_clamp) return;
    s_clamp = false;
    for (DWORD stage = 0; stage < 8; stage++)
        for (int i = 0; i < 2; i++)
            s_origSetSamplerState(s_device, stage, i ? D3DSAMP_ADDRESSV : D3DSAMP_ADDRESSU, s_wantAddress[stage][i]);
}

static HRESULT STDMETHODCALLTYPE Hook_SetSamplerState(IDirect3DDevice9* dev, DWORD stage, D3DSAMPLERSTATETYPE type, DWORD value)
{
    if (s_clamp && stage < 8 && (type == D3DSAMP_ADDRESSU || type == D3DSAMP_ADDRESSV)) {
        s_wantAddress[stage][type == D3DSAMP_ADDRESSV] = value;
        return s_origSetSamplerState(dev, stage, type, D3DTADDRESS_CLAMP);
    }
    if (stage < 16 && type == D3DSAMP_MINFILTER) {
        s_stages[stage].minFilter = value;
        ApplyFilter(dev, stage);
        return D3D_OK;
    }
    if (stage < 16 && type == D3DSAMP_MAXMIPLEVEL && g_cfg.textureDetail > 0) {
        s_stages[stage].maxMip = value;
        ApplyMipLimit(dev, stage);
        return D3D_OK;
    }
    if (stage < 16 && type == D3DSAMP_MAXANISOTROPY && g_cfg.anisotropy > 1 && s_stages[stage].eligible &&
        value < (DWORD)g_cfg.anisotropy)
        value = (DWORD)g_cfg.anisotropy;
    return s_origSetSamplerState(dev, stage, type, value);
}

// the device's slots hooked here besides Present and Reset (RepatchSlots)
struct SlotHook {
    int index;
    void* hook;
    void** orig;
};
static SlotHook s_slotHooks[32];
static int s_slotHookCount;

static void PatchVtable(void** vt, int index, void* hook, void** orig)
{
    if (*orig) return;
    *orig = vt[index];
    DWORD old;
    VirtualProtect(&vt[index], sizeof(void*), PAGE_EXECUTE_READWRITE, &old);
    vt[index] = hook;
    VirtualProtect(&vt[index], sizeof(void*), old, &old);
    if (index != 16 && index != 17 && s_slotHookCount < 32) s_slotHooks[s_slotHookCount++] = { index, hook, orig };
}

// ---------------------------------------------------------------- capture and log

// test feed: save the back buffer as JPG next to the game on request (exclusive fullscreen
// defeats desktop capture)
static void CaptureBackBuffer(IDirect3DDevice9* dev, DWORD index)
{
    static D3DXSaveSurfaceToFileA_t save;
    if (!save) {
        HMODULE x = GetModuleHandleA("d3dx9_42.dll");
        save = x ? (D3DXSaveSurfaceToFileA_t)GetProcAddress(x, "D3DXSaveSurfaceToFileA") : NULL;
        if (!save) return;
    }
    IDirect3DSurface9 *bb = NULL, *sys = NULL;
    D3DSURFACE_DESC d;
    if (SUCCEEDED(dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb)) && SUCCEEDED(bb->GetDesc(&d)) &&
        SUCCEEDED(dev->CreateOffscreenPlainSurface(d.Width, d.Height, d.Format, D3DPOOL_SYSTEMMEM, &sys, NULL)) &&
        SUCCEEDED(dev->GetRenderTargetData(bb, sys))) {
        char name[48], path[MAX_PATH];
        wsprintfA(name, "SplitSecondPad_shot%lu.jpg", index);
        ModulePath(path, name);
        HRESULT hr = save(path, 1 /* D3DXIFF_JPG */, sys, NULL, NULL);
        Log("capture %s -> %08lx", path, hr);
    }
    if (sys) sys->Release();
    if (bb) bb->Release();
}

// ---------------------------------------------------------------- frame dump (Log=1, F9)

// One frame, pass by pass: whenever the game switches render target 0, the target it drew into is
// saved (a JPG of all of it, a PNG of its bottom 64 rows) and the mean colour of its bottom rows is
// logged, with the targets and textures each pass reads. StretchRect copies are logged and their
// destination saved. Only reads; the device state the game set is never changed.
typedef HRESULT(STDMETHODCALLTYPE* SetRenderTarget_t)(IDirect3DDevice9*, DWORD, IDirect3DSurface9*);
typedef HRESULT(STDMETHODCALLTYPE* StretchRect_t)(IDirect3DDevice9*, IDirect3DSurface9*, const RECT*, IDirect3DSurface9*,
                                                  const RECT*, D3DTEXTUREFILTERTYPE);
typedef HRESULT(STDMETHODCALLTYPE* DrawPrimitive_t)(IDirect3DDevice9*, D3DPRIMITIVETYPE, UINT, UINT);
typedef HRESULT(STDMETHODCALLTYPE* DrawIndexedPrimitive_t)(IDirect3DDevice9*, D3DPRIMITIVETYPE, INT, UINT, UINT, UINT, UINT);
typedef HRESULT(STDMETHODCALLTYPE* DrawPrimitiveUP_t)(IDirect3DDevice9*, D3DPRIMITIVETYPE, UINT, const void*, UINT);
typedef HRESULT(STDMETHODCALLTYPE* DrawIndexedPrimitiveUP_t)(IDirect3DDevice9*, D3DPRIMITIVETYPE, UINT, UINT, UINT, const void*,
                                                             D3DFORMAT, const void*, UINT);
static SetRenderTarget_t s_origSetRenderTarget;
static StretchRect_t s_origStretchRect;
static DrawPrimitive_t s_origDrawPrimitive;
static DrawIndexedPrimitive_t s_origDrawIndexedPrimitive;
static DrawPrimitiveUP_t s_origDrawPrimitiveUP;
static DrawIndexedPrimitiveUP_t s_origDrawIndexedPrimitiveUP;

static bool s_dumping;     // the current frame is dumped
static DWORD s_dumpIndex;  // F9 presses so far
static int s_pass;         // render target switches in the dumped frame
static DWORD s_passDraws;  // draws into the current target
static char s_dumpDir[MAX_PATH];

static float Half(WORD h)
{
    DWORD sign = (h >> 15) & 1, exp = (h >> 10) & 31, man = h & 1023;
    float v = exp ? (1.0f + man / 1024.0f) * (float)(1 << exp) / 32768.0f : man / 1024.0f / 16384.0f;
    if (exp == 31) v = 65504.0f;
    return sign ? -v : v;
}

// mean colour of one row of a locked surface, false for formats not read here
static bool RowMean(const D3DLOCKED_RECT& lr, D3DFORMAT fmt, UINT width, UINT y, float out[4])
{
    const BYTE* row = (const BYTE*)lr.pBits + (size_t)y * lr.Pitch;
    double sum[4] = {};
    for (UINT x = 0; x < width; x++) {
        float c[4];
        if (fmt == D3DFMT_A8R8G8B8 || fmt == D3DFMT_X8R8G8B8) {
            DWORD p = ((const DWORD*)row)[x];
            c[0] = ((p >> 16) & 255) / 255.0f, c[1] = ((p >> 8) & 255) / 255.0f, c[2] = (p & 255) / 255.0f, c[3] = (p >> 24) / 255.0f;
        } else if (fmt == D3DFMT_A2R10G10B10) {
            DWORD p = ((const DWORD*)row)[x];
            c[0] = ((p >> 20) & 1023) / 1023.0f, c[1] = ((p >> 10) & 1023) / 1023.0f, c[2] = (p & 1023) / 1023.0f, c[3] = (p >> 30) / 3.0f;
        } else if (fmt == D3DFMT_A16B16G16R16F) {
            const WORD* p = (const WORD*)row + x * 4;
            for (int i = 0; i < 4; i++) c[i] = Half(p[i]);
        } else if (fmt == D3DFMT_R32F) {
            c[0] = ((const float*)row)[x], c[1] = c[2] = c[3] = 0.0f;
        } else if (fmt == D3DFMT_G16R16F) {
            const WORD* p = (const WORD*)row + x * 2;
            c[0] = Half(p[0]), c[1] = Half(p[1]), c[2] = c[3] = 0.0f;
        } else {
            return false;
        }
        for (int i = 0; i < 4; i++) sum[i] += c[i];
    }
    for (int i = 0; i < 4; i++) out[i] = (float)(sum[i] / width);
    return true;
}

static void DumpSurface(IDirect3DDevice9* dev, IDirect3DSurface9* surf, const char* what)
{
    static D3DXSaveSurfaceToFileA_t save;
    if (!save) {
        HMODULE x = GetModuleHandleA("d3dx9_42.dll");
        save = x ? (D3DXSaveSurfaceToFileA_t)GetProcAddress(x, "D3DXSaveSurfaceToFileA") : NULL;
    }
    D3DSURFACE_DESC d;
    if (!surf || FAILED(surf->GetDesc(&d))) return;
    IDirect3DSurface9 *resolved = NULL, *sys = NULL, *src = surf;
    HRESULT hr = D3D_OK;
    // a multisampled target is resolved into a plain one first
    if (d.MultiSampleType != D3DMULTISAMPLE_NONE) {
        hr = dev->CreateRenderTarget(d.Width, d.Height, d.Format, D3DMULTISAMPLE_NONE, 0, FALSE, &resolved, NULL);
        if (SUCCEEDED(hr)) hr = s_origStretchRect(dev, surf, NULL, resolved, NULL, D3DTEXF_NONE);
        src = resolved;
    }
    if (SUCCEEDED(hr)) hr = dev->CreateOffscreenPlainSurface(d.Width, d.Height, d.Format, D3DPOOL_SYSTEMMEM, &sys, NULL);
    if (SUCCEEDED(hr)) hr = dev->GetRenderTargetData(src, sys);
    char name[96], path[MAX_PATH];
    wsprintfA(name, "f%lu_%02d_%s_%ux%u_fmt%u", s_dumpIndex, s_pass, what, d.Width, d.Height, d.Format);
    if (FAILED(hr)) {
        Log("dump %s: not readable (%08lx), multisample %d, usage %lx, pool %d", name, hr, d.MultiSampleType, d.Usage, d.Pool);
    } else {
        D3DLOCKED_RECT lr;
        if (SUCCEEDED(sys->LockRect(&lr, NULL, D3DLOCK_READONLY))) {
            char line[512];
            int n = wsprintfA(line, "dump %s: row means r g b a (x1000)", name);
            UINT rows[] = { d.Height - 1, d.Height - 2, d.Height - 3, d.Height - 4, d.Height - 6, d.Height - 8, d.Height - 16, d.Height / 2 };
            for (UINT y : rows) {
                float m[4];
                if (y >= d.Height || !RowMean(lr, d.Format, d.Width, y, m)) continue;
                n += wsprintfA(line + n, " | y%u %d %d %d %d", y, (int)(m[0] * 1000), (int)(m[1] * 1000), (int)(m[2] * 1000),
                               (int)(m[3] * 1000));
                if (n > 440) break;
            }
            sys->UnlockRect();
            Log("%s", line);
        }
        if (save) {
            wsprintfA(path, "%s\\%s.jpg", s_dumpDir, name);
            HRESULT h1 = save(path, 1 /* D3DXIFF_JPG */, sys, NULL, NULL);
            RECT bottom = { 0, d.Height > 64 ? (LONG)d.Height - 64 : 0, (LONG)d.Width, (LONG)d.Height };
            wsprintfA(path, "%s\\%s_bottom.png", s_dumpDir, name);
            HRESULT h2 = save(path, 3 /* D3DXIFF_PNG */, sys, NULL, &bottom);
            if (FAILED(h2)) {
                wsprintfA(path, "%s\\%s_bottom.dds", s_dumpDir, name);
                h2 = save(path, 4 /* D3DXIFF_DDS */, sys, NULL, &bottom);
            }
            if (FAILED(h1) || FAILED(h2)) Log("dump %s: saving failed (%08lx %08lx)", name, h1, h2);
        }
    }
    if (sys) sys->Release();
    if (resolved) resolved->Release();
}

static void DescribeSurface(IDirect3DSurface9* s, char* out)
{
    D3DSURFACE_DESC d;
    IDirect3DTexture9* tex = NULL;
    if (s && SUCCEEDED(s->GetContainer(IID_IDirect3DTexture9, (void**)&tex)) && tex) tex->Release();
    if (s && SUCCEEDED(s->GetDesc(&d)))
        wsprintfA(out, "%p (texture %p) %ux%u fmt %u ms %d", s, tex, d.Width, d.Height, d.Format, d.MultiSampleType);
    else
        wsprintfA(out, "%p", s);
}

static void ShadowPassStart(IDirect3DDevice9* dev);

// at the first draw into a target: what it is and what the pass reads
static void DumpPassStart(IDirect3DDevice9* dev)
{
    IDirect3DSurface9* rt = NULL;
    dev->GetRenderTarget(0, &rt);
    char desc[128];
    DescribeSurface(rt, desc);
    if (rt) rt->Release();
    D3DVIEWPORT9 vp = {};
    dev->GetViewport(&vp);
    DWORD scissor = 0;
    dev->GetRenderState(D3DRS_SCISSORTESTENABLE, &scissor);
    RECT sr = {};
    dev->GetScissorRect(&sr);
    IDirect3DPixelShader9* ps = NULL;
    dev->GetPixelShader(&ps);
    if (ps) ps->Release();
    Log("dump pass %02d: target %s, viewport %lu,%lu %lux%lu, scissor %lu (%ld,%ld-%ld,%ld), pixel shader %p", s_pass, desc, vp.X,
        vp.Y, vp.Width, vp.Height, scissor, sr.left, sr.top, sr.right, sr.bottom, ps);
    for (DWORD stage = 0; stage < 8; stage++) {
        IDirect3DBaseTexture9* t = NULL;
        if (FAILED(dev->GetTexture(stage, &t)) || !t) continue;
        D3DSURFACE_DESC d = {};
        if (t->GetType() == D3DRTYPE_TEXTURE) ((IDirect3DTexture9*)t)->GetLevelDesc(0, &d);
        DWORD u = 0, v = 0, mag = 0;
        dev->GetSamplerState(stage, D3DSAMP_ADDRESSU, &u);
        dev->GetSamplerState(stage, D3DSAMP_ADDRESSV, &v);
        dev->GetSamplerState(stage, D3DSAMP_MAGFILTER, &mag);
        Log("dump pass %02d:   stage %lu texture %p %ux%u fmt %u usage %lx, address %lu/%lu, filter %lu", s_pass, stage, t, d.Width,
            d.Height, d.Format, d.Usage, u, v, mag);
        t->Release();
    }
    ShadowPassStart(dev);
}

static void ShadowDrawCheck(IDirect3DDevice9* dev);

static void CountDraw(IDirect3DDevice9* dev)
{
    s_drawsSeen++;
    if (!s_dumping) return;
    if (!s_passDraws) DumpPassStart(dev);
    ShadowDrawCheck(dev);
    s_passDraws++;
}

void DumpMark(const char* what)
{
    if (s_dumping) Log("dump pass %02d: -- %s (after %lu draws)", s_pass, what, s_passDraws);
}

static HRESULT STDMETHODCALLTYPE Hook_DrawPrimitive(IDirect3DDevice9* dev, D3DPRIMITIVETYPE t, UINT a, UINT b)
{
    CountDraw(dev);
    return s_origDrawPrimitive(dev, t, a, b);
}

static HRESULT STDMETHODCALLTYPE Hook_DrawIndexedPrimitive(IDirect3DDevice9* dev, D3DPRIMITIVETYPE t, INT a, UINT b, UINT c, UINT d, UINT e)
{
    CountDraw(dev);
    return s_origDrawIndexedPrimitive(dev, t, a, b, c, d, e);
}

static HRESULT STDMETHODCALLTYPE Hook_DrawPrimitiveUP(IDirect3DDevice9* dev, D3DPRIMITIVETYPE t, UINT a, const void* b, UINT c)
{
    CountDraw(dev);
    return s_origDrawPrimitiveUP(dev, t, a, b, c);
}

static HRESULT STDMETHODCALLTYPE Hook_DrawIndexedPrimitiveUP(IDirect3DDevice9* dev, D3DPRIMITIVETYPE t, UINT a, UINT b, UINT c, const void* d,
                                                             D3DFORMAT e, const void* f, UINT g)
{
    CountDraw(dev);
    return s_origDrawIndexedPrimitiveUP(dev, t, a, b, c, d, e, f, g);
}

static bool ScaledShadow(IDirect3DSurface9* s);

static void EndPass(IDirect3DDevice9* dev, const char* why)
{
    if (s_passDraws) {
        IDirect3DSurface9* rt = NULL;
        if (SUCCEEDED(dev->GetRenderTarget(0, &rt)) && rt) {
            Log("dump pass %02d: %lu draws, ends (%s)", s_pass, s_passDraws, why);
            if (!ScaledShadow(rt)) DumpSurface(dev, rt, "target");  // the scaled shadow colour target is never written
            rt->Release();
        }
        s_pass++;
    }
    s_passDraws = 0;
}

// The full screen passes (bloom and tone mapping, motion blur, depth of field, colour grading ...) all
// draw through one helper (0x511BA0) that copies a quad template ([[0xD67758]+4], 4 vertices of 7
// floats: position xyz, texture coordinates) and moves it by half a pixel plus a fixed 0.0005 up and
// 0.00015 left in clip space. The fixed part is a fraction of a pixel at the resolutions the port was
// made for, but at graphics quality Very High on a 4K screen the passes render at 7680x4320, where it
// adds up to more than a pixel: every pass moves the picture up by a row and leaves its bottom row
// unwritten, so whatever was drawn there once stays (a line at the bottom of the screen). For targets
// above 2160 rows (3840 columns) the template is moved back by the part of the fixed offset that goes
// beyond what it is at 2160 rows (3840 columns), so it never exceeds the fraction of a pixel it is at
// native 4K. Only the template's position values are written; below that size nothing changes.
static float s_quadOrigY[4], s_quadOrigX[4], s_quadSetY[4], s_quadSetX[4];
static float* s_quad;

static void FitQuadOffset(IDirect3DSurface9* surf)
{
    D3DSURFACE_DESC d;
    if (!KnownBuild() || !surf || FAILED(surf->GetDesc(&d))) return;  // other builds keep other data there
    BYTE* holder = *(BYTE**)G(0x00D67758);
    float* quad = holder ? *(float**)(holder + 4) : NULL;
    if (!quad || IsBadReadPtr(quad, 0x70)) return;
    // not inside the executable's code or constants
    if ((DWORD)quad >= G(0x00401000) && (DWORD)quad < G(0x00CF5000)) return;
    if (quad != s_quad) {
        for (int i = 0; i < 4; i++) {
            float x = quad[i * 7], y = quad[i * 7 + 1];
            if (x < -1.01f || x > 1.01f || y < -1.01f || y > 1.01f) {
                static bool logged;
                if (!logged) Log("full screen quad template not recognised (%.4f %.4f)", x, y);
                logged = true;
                return;
            }
            s_quadOrigX[i] = s_quadSetX[i] = x;
            s_quadOrigY[i] = s_quadSetY[i] = y;
        }
        s_quad = quad;
        Log("full screen quad template at %p: %.4f %.4f / %.4f %.4f / %.4f %.4f / %.4f %.4f", quad, quad[0], quad[1], quad[7], quad[8],
            quad[14], quad[15], quad[21], quad[22]);
    }
    float dy = d.Height > 2160 ? 0.0005f * (1.0f - 2160.0f / d.Height) : 0.0f;
    float dx = d.Width > 3840 ? 0.00015f * (1.0f - 3840.0f / d.Width) : 0.0f;
    for (int i = 0; i < 4; i++) {
        float* x = &quad[i * 7];
        float* y = &quad[i * 7 + 1];
        if (*x != s_quadSetX[i]) s_quadOrigX[i] = *x;  // the game wrote it meanwhile
        if (*y != s_quadSetY[i]) s_quadOrigY[i] = *y;
        s_quadSetX[i] = s_quadOrigX[i] + dx;
        s_quadSetY[i] = s_quadOrigY[i] - dy;
        if (*x != s_quadSetX[i]) *x = s_quadSetX[i];
        if (*y != s_quadSetY[i]) *y = s_quadSetY[i];
    }
    static bool logged;
    if ((dx || dy) && !logged) {
        logged = true;
        Log("full screen passes at %ux%u: quad offset reduced by %.6f up, %.6f left", d.Width, d.Height, dy, dx);
    }
}

// ---------------------------------------------------------------- shadow map resolution (ShadowResolution)

// The sun shadows go into one depth texture of 1024x3072 (INTZ): three cascades of 1024x1024 one above the
// other, drawn through viewports at y = 0, 1024 and 2048, with a colour target of the same size bound that
// is never written (colour writes off). The game never asks either for its size, and its shaders find a
// cascade and filter it in texture coordinates (0 to 1), so the map can be larger: both are created
// ShadowResolution times as wide and high, and while the colour target is render target 0 the viewports,
// scissor rects and clear rects the game sets in its 1024x3072 units are scaled with them. The soft edge
// keeps its width and is sampled finer. Only the creation calls and those rectangles change; the game's
// code and data are untouched.
// Both targets must keep the same size: the factor is lowered to what the device's largest texture
// allows, and once a large one cannot be made (the game makes the colour target first), every shadow
// target from then on is made at the game's size and nothing is scaled, so the shadows are as the game's.
static const UINT kShadowW = 1024, kShadowH = 3072;
static UINT s_shadowFactor = 1;  // set when the device is made
static bool s_shadowFailed;      // a large shadow target could not be made
static bool s_shadowRt;          // render target 0 is the scaled shadow colour target

static void ShadowFactorFor(IDirect3DDevice9* dev)
{
    s_shadowFactor = (UINT)g_cfg.shadowResolution;
    D3DCAPS9 caps;
    if (s_shadowFactor > 1 && SUCCEEDED(dev->GetDeviceCaps(&caps)))
        while (s_shadowFactor > 1 && (kShadowW * s_shadowFactor > caps.MaxTextureWidth || kShadowH * s_shadowFactor > caps.MaxTextureHeight))
            s_shadowFactor /= 2;
    if (s_shadowFactor != (UINT)g_cfg.shadowResolution)
        Log("shadow maps: %ux resolution is beyond this device's largest texture, %ux instead", g_cfg.shadowResolution, s_shadowFactor);
}

// the game's choice of depth format for it varies by device (INTZ, RAWZ, DF24, DF16 or D24S8)
static bool ShadowAtlasRequest(UINT w, UINT h, UINT levels, DWORD usage, D3DFORMAT fmt, D3DPOOL pool)
{
    return s_shadowFactor > 1 && !s_shadowFailed && w == kShadowW && h == kShadowH && levels == 1 && pool == D3DPOOL_DEFAULT &&
           (usage == D3DUSAGE_DEPTHSTENCIL || (usage == D3DUSAGE_RENDERTARGET && fmt == D3DFMT_A8R8G8B8));
}

static bool ScaledShadow(IDirect3DSurface9* s)
{
    D3DSURFACE_DESC d;
    UINT f = s_shadowFactor;
    return f > 1 && !s_shadowFailed && s && SUCCEEDED(s->GetDesc(&d)) && d.Width == kShadowW * f && d.Height == kShadowH * f;
}

// a rectangle in the game's units (inside 1024x3072) while the scaled target is bound; one already beyond
// that (read back from the device and set again) stays as it is
static bool ScaleShadowRect(LONG& x0, LONG& y0, LONG& x1, LONG& y1)
{
    if (!s_shadowRt || x0 < 0 || y0 < 0 || x1 > (LONG)kShadowW || y1 > (LONG)kShadowH) return false;
    LONG f = (LONG)s_shadowFactor;
    x0 *= f, y0 *= f, x1 *= f, y1 *= f;
    return true;
}

static HRESULT STDMETHODCALLTYPE Hook_SetRenderTarget(IDirect3DDevice9* dev, DWORD index, IDirect3DSurface9* surf)
{
    if (index == 0) FitQuadOffset(surf);
    if (index == 0 && s_shadowFactor > 1) s_shadowRt = ScaledShadow(surf);
    if (s_dumping && s_pass < 120) {
        IDirect3DSurface9* cur = NULL;
        dev->GetRenderTarget(index, &cur);
        if (cur) cur->Release();
        if (cur != surf) {
            if (index == 0) {
                EndPass(dev, "new target");
            } else {
                char desc[128];
                DescribeSurface(surf, desc);
                Log("dump pass %02d: render target %lu = %s", s_pass, index, desc);
            }
        }
    }
    return s_origSetRenderTarget(dev, index, surf);
}

static HRESULT STDMETHODCALLTYPE Hook_StretchRect(IDirect3DDevice9* dev, IDirect3DSurface9* src, const RECT* sr, IDirect3DSurface9* dst,
                                                  const RECT* dr, D3DTEXTUREFILTERTYPE filter)
{
    HRESULT hr = s_origStretchRect(dev, src, sr, dst, dr, filter);
    if (s_dumping && s_pass < 120) {
        char a[128], b[128];
        DescribeSurface(src, a);
        DescribeSurface(dst, b);
        Log("dump pass %02d: StretchRect %s (%ld,%ld-%ld,%ld) -> %s (%ld,%ld-%ld,%ld) filter %d -> %08lx", s_pass, a, sr ? sr->left : -1,
            sr ? sr->top : -1, sr ? sr->right : -1, sr ? sr->bottom : -1, b, dr ? dr->left : -1, dr ? dr->top : -1, dr ? dr->right : -1,
            dr ? dr->bottom : -1, filter, hr);
        DumpSurface(dev, dst, "copy");
    }
    return hr;
}

// ---------------------------------------------------------------- shadow map survey (Log=1)

// What a larger shadow map would have to take into account, read off the running game: the render
// targets and depth surfaces it creates (size, format, the game's call chain), the viewports, scissor
// rects and clears of the dumped frame, the depth surface and depth bias of the passes into shadow-sized
// targets, the shader constants and shaders of the draws that read a shadow map, and the places in the
// game that ask a shadow-sized texture or surface for its size. Only logs; every call goes through as made.
typedef HRESULT(STDMETHODCALLTYPE* CreateTexture_t)(IDirect3DDevice9*, UINT, UINT, UINT, DWORD, D3DFORMAT, D3DPOOL, IDirect3DTexture9**,
                                                    HANDLE*);
typedef HRESULT(STDMETHODCALLTYPE* CreateCubeTexture_t)(IDirect3DDevice9*, UINT, UINT, DWORD, D3DFORMAT, D3DPOOL, IDirect3DCubeTexture9**,
                                                        HANDLE*);
typedef HRESULT(STDMETHODCALLTYPE* CreateSurface_t)(IDirect3DDevice9*, UINT, UINT, D3DFORMAT, D3DMULTISAMPLE_TYPE, DWORD, BOOL,
                                                    IDirect3DSurface9**, HANDLE*);
typedef HRESULT(STDMETHODCALLTYPE* SetViewport_t)(IDirect3DDevice9*, const D3DVIEWPORT9*);
typedef HRESULT(STDMETHODCALLTYPE* SetScissorRect_t)(IDirect3DDevice9*, const RECT*);
typedef HRESULT(STDMETHODCALLTYPE* SetDepthStencilSurface_t)(IDirect3DDevice9*, IDirect3DSurface9*);
typedef HRESULT(STDMETHODCALLTYPE* Clear_t)(IDirect3DDevice9*, DWORD, const D3DRECT*, DWORD, D3DCOLOR, float, DWORD);
typedef HRESULT(STDMETHODCALLTYPE* TexGetLevelDesc_t)(IDirect3DTexture9*, UINT, D3DSURFACE_DESC*);
typedef HRESULT(STDMETHODCALLTYPE* SurfGetDesc_t)(IDirect3DSurface9*, D3DSURFACE_DESC*);
static CreateTexture_t s_origCreateTexture;
static CreateCubeTexture_t s_origCreateCubeTexture;
static CreateSurface_t s_origCreateRenderTarget, s_origCreateDepthStencil;
static SetViewport_t s_origSetViewport;
static SetScissorRect_t s_origSetScissorRect;
static SetDepthStencilSurface_t s_origSetDepthStencil;
static Clear_t s_origClear;
static int s_createLogs, s_frameLogs;  // lines so far (creation for the whole run, the rest per dump)
static int s_constLines;               // shader constant lines in this dump
static bool s_viewportLogged, s_scissorLogged;  // in this dump
static void* s_constLogged[24];        // pixel shaders described in this dump
static int s_constLoggedCount;
static CRITICAL_SECTION s_surveyLock;  // resources may be created and asked for their size on more than one thread
static DWORD s_gameLo, s_gameHi;       // the executable's image, set before the hooks go in

static bool ShadowSize(UINT w, UINT h)
{
    UINT f = s_shadowFactor;
    return (w == 1024 && h == 3072) || (w == 3072 && h == 1024) || (w == h && (w == 512 || w == 1024 || w == 2048)) ||
           (f > 1 && w == 1024 * f && h == 3072 * f);
}

static DWORD s_codeLo, s_codeHi;       // its code section (.text), the only part of it read here

static bool InGame(DWORD a) { return a >= s_gameLo && a < s_gameHi; }

static void SurveyInit()
{
    static bool done;  // once, also if the game makes a second device
    if (done) return;
    done = true;
    InitializeCriticalSection(&s_surveyLock);
    BYTE* base = (BYTE*)GetModuleHandleA(NULL);
    IMAGE_DOS_HEADER* dos = (IMAGE_DOS_HEADER*)base;
    if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew <= 0 || dos->e_lfanew > 0x1000) return;  // nothing counts as the game
    IMAGE_NT_HEADERS* nt = (IMAGE_NT_HEADERS*)(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return;
    IMAGE_SECTION_HEADER* sec = IMAGE_FIRST_SECTION(nt);
    for (WORD i = 0; i < nt->FileHeader.NumberOfSections && i < 32; i++)
        if (!memcmp(sec[i].Name, ".text", 6)) {
            s_codeLo = (DWORD)base + sec[i].VirtualAddress;
            s_codeHi = s_codeLo + sec[i].Misc.VirtualSize;
        }
    s_gameHi = (DWORD)base + nt->OptionalHeader.SizeOfImage;
    s_gameLo = (DWORD)base;
}

// A return address in the game: a value on the stack inside the executable's code right after a call
// instruction (E8 rel32, FF /2 with a register, [reg], [reg+disp8], [disp32] or [reg+disp32]).
static bool AfterCall(DWORD a)
{
    if (a < s_codeLo + 6 || a >= s_codeHi) return false;
    const BYTE* p = (const BYTE*)a;
    __try {  // a page of it that is not readable is not a return address either
        if (p[-5] == 0xE8) return true;
        if (p[-2] == 0xFF && (p[-1] & 0x38) == 0x10) return true;
        if (p[-3] == 0xFF && (p[-2] & 0x38) == 0x10) return true;
        if (p[-6] == 0xFF && (p[-5] & 0x38) == 0x10) return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return false;
}

// The game's part of the call chain above a hook (the immediate caller is often a wrapper of the
// engine's): up to four return addresses, found by walking up the stack from the hook's frame.
static int GameChain(const void* ret, DWORD* out)
{
    int n = 0;
    if (InGame((DWORD)ret)) out[n++] = (DWORD)ret;
    else return 0;  // not called by the game
    const DWORD* sp = (const DWORD*)_AddressOfReturnAddress();
    const DWORD* top = (const DWORD*)((NT_TIB*)NtCurrentTeb())->StackBase;  // never past the thread's stack
    for (int i = 1; i < 160 && n < 4 && sp + i < top; i++) {
        DWORD v = sp[i];
        bool again = false;  // a stale copy further up the stack
        for (int k = 0; k < n; k++) again |= out[k] == v;
        if (!again && AfterCall(v)) out[n++] = v;
    }
    return n;
}

static void FormatChain(const DWORD* c, int n, char* out)
{
    int k = 0;
    out[0] = 0;
    for (int i = 0; i < n; i++) k += wsprintfA(out + k, i ? " < %08lx" : "%08lx", c[i]);
}

// The size queries: hooked in the vtables of the Direct3D runtime's texture and surface classes (shared by
// all objects of a class, so each vtable seen once), logged once per call chain in the game.
struct VtHook {
    void** vt;
    void* orig;
};
static VtHook s_levelDescHooks[4], s_surfDescHooks[6];
static DWORD s_sizeCallers[64][2];
static int s_sizeCallerCount;

static void* OrigFor(VtHook* hooks, int n, void* self)
{
    void** vt = *(void***)self;
    for (int i = 0; i < n; i++)
        if (hooks[i].vt == vt) return hooks[i].orig;
    return NULL;
}

static void NoteSizeQuery(const void* ret, const char* what, const D3DSURFACE_DESC& d)
{
    if (!InGame((DWORD)ret) || !ShadowSize(d.Width, d.Height) || !(d.Usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL))) return;
    DWORD chain[4] = {};
    int n = GameChain(ret, chain);
    DWORD second = n > 1 ? chain[1] : 0;
    EnterCriticalSection(&s_surveyLock);
    bool seen = false;
    for (int i = 0; i < s_sizeCallerCount && !seen; i++) seen = s_sizeCallers[i][0] == chain[0] && s_sizeCallers[i][1] == second;
    if (!seen && s_sizeCallerCount < 64) {
        s_sizeCallers[s_sizeCallerCount][0] = chain[0];
        s_sizeCallers[s_sizeCallerCount][1] = second;
        s_sizeCallerCount++;
        char c[64];
        FormatChain(chain, n, c);
        Log("shadow survey: the game asks %s for its size %ux%u fmt %u usage %lx, from %s", what, d.Width, d.Height, d.Format, d.Usage, c);
    }
    LeaveCriticalSection(&s_surveyLock);
}

static HRESULT STDMETHODCALLTYPE Spy_GetLevelDesc(IDirect3DTexture9* self, UINT level, D3DSURFACE_DESC* d)
{
    TexGetLevelDesc_t orig = (TexGetLevelDesc_t)OrigFor(s_levelDescHooks, 4, self);
    HRESULT hr = orig ? orig(self, level, d) : D3DERR_INVALIDCALL;
    if (SUCCEEDED(hr) && d) NoteSizeQuery(_ReturnAddress(), "a texture (GetLevelDesc)", *d);
    return hr;
}

static HRESULT STDMETHODCALLTYPE Spy_SurfGetDesc(IDirect3DSurface9* self, D3DSURFACE_DESC* d)
{
    SurfGetDesc_t orig = (SurfGetDesc_t)OrigFor(s_surfDescHooks, 6, self);
    HRESULT hr = orig ? orig(self, d) : D3DERR_INVALIDCALL;
    if (SUCCEEDED(hr) && d) NoteSizeQuery(_ReturnAddress(), "a surface (GetDesc)", *d);
    return hr;
}

// under the lock, so two threads never take the same entry or note the spy as the original
static void SpyOn(VtHook* hooks, int n, void* obj, int slot, void* spy)
{
    void** vt = *(void***)obj;
    EnterCriticalSection(&s_surveyLock);
    for (int i = 0; i < n; i++) {
        if (hooks[i].vt == vt) break;
        if (!hooks[i].vt) {
            if (vt[slot] == spy) break;  // a class sharing the vtable slot's code is hooked already
            // the original is noted before the slot changes, so a call meanwhile finds it
            hooks[i].orig = vt[slot];
            hooks[i].vt = vt;
            DWORD old;
            VirtualProtect(&vt[slot], sizeof(void*), PAGE_EXECUTE_READWRITE, &old);
            vt[slot] = spy;
            VirtualProtect(&vt[slot], sizeof(void*), old, &old);
            break;
        }
    }
    LeaveCriticalSection(&s_surveyLock);
}

static const char* PoolName(D3DPOOL p)
{
    return p == D3DPOOL_DEFAULT ? "default" : p == D3DPOOL_MANAGED ? "managed" : p == D3DPOOL_SYSTEMMEM ? "sysmem" : "scratch";
}

static bool CreateLog() { return InterlockedIncrement((LONG*)&s_createLogs) <= 400; }

static HRESULT STDMETHODCALLTYPE Hook_CreateTexture(IDirect3DDevice9* dev, UINT w, UINT h, UINT levels, DWORD usage, D3DFORMAT fmt,
                                                    D3DPOOL pool, IDirect3DTexture9** out, HANDLE* shared)
{
    HRESULT hr;
    if (ShadowAtlasRequest(w, h, levels, usage, fmt, pool)) {
        UINT f = s_shadowFactor;
        hr = s_origCreateTexture(dev, w * f, h * f, levels, usage, fmt, pool, out, shared);
        static int logged;
        if (FAILED(hr)) {
            s_shadowFailed = true;  // from now on all at the game's size, nothing scaled
            s_shadowRt = false;
            hr = s_origCreateTexture(dev, w, h, levels, usage, fmt, pool, out, shared);  // as the game asked
            if (logged++ < 4)
                Log("shadow map: %ux%u (format %u) could not be made (%08lx), the game's size from now on", w * f, h * f, fmt, hr);
        } else if (logged++ < 4) {
            Log("shadow map: %ux%u instead of %ux%u (format %u)", w * f, h * f, w, h, fmt);
        }
    } else {
        hr = s_origCreateTexture(dev, w, h, levels, usage, fmt, pool, out, shared);
    }
    InterlockedIncrement(&s_createSeen);
    DWORD chain[4] = {};
    int n;
    if (g_cfg.log && (usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL)) && (n = GameChain(_ReturnAddress(), chain)) > 0) {
        if (CreateLog()) {
            char c[64];
            FormatChain(chain, n, c);
            Log("shadow survey: CreateTexture %ux%u levels %u usage %lx fmt %u pool %s -> %08lx %p, from %s", w, h, levels, usage, fmt,
                PoolName(pool), hr, SUCCEEDED(hr) && out ? *out : NULL, c);
        }
        if (SUCCEEDED(hr) && out && *out) {
            SpyOn(s_levelDescHooks, 4, *out, 17, (void*)Spy_GetLevelDesc);
            IDirect3DSurface9* level = NULL;
            if (SUCCEEDED((*out)->GetSurfaceLevel(0, &level)) && level) {
                SpyOn(s_surfDescHooks, 6, level, 12, (void*)Spy_SurfGetDesc);
                level->Release();
            }
        }
    }
    return hr;
}

static HRESULT STDMETHODCALLTYPE Hook_CreateCubeTexture(IDirect3DDevice9* dev, UINT edge, UINT levels, DWORD usage, D3DFORMAT fmt,
                                                        D3DPOOL pool, IDirect3DCubeTexture9** out, HANDLE* shared)
{
    HRESULT hr = s_origCreateCubeTexture(dev, edge, levels, usage, fmt, pool, out, shared);
    DWORD chain[4] = {};
    int n;
    if ((usage & (D3DUSAGE_RENDERTARGET | D3DUSAGE_DEPTHSTENCIL)) && (n = GameChain(_ReturnAddress(), chain)) > 0 && CreateLog()) {
        char c[64];
        FormatChain(chain, n, c);
        Log("shadow survey: CreateCubeTexture %u levels %u usage %lx fmt %u pool %s -> %08lx %p, from %s", edge, levels, usage, fmt,
            PoolName(pool), hr, SUCCEEDED(hr) && out ? *out : NULL, c);
    }
    return hr;
}

static void LogSurfaceCreate(const char* what, UINT w, UINT h, D3DFORMAT fmt, D3DMULTISAMPLE_TYPE ms, HRESULT hr,
                             IDirect3DSurface9** out, const void* ret)
{
    DWORD chain[4] = {};
    int n = GameChain(ret, chain);
    if (!n) return;
    if (CreateLog()) {
        char c[64];
        FormatChain(chain, n, c);
        Log("shadow survey: %s %ux%u fmt %u ms %d -> %08lx %p, from %s", what, w, h, fmt, ms, hr, SUCCEEDED(hr) && out ? *out : NULL, c);
    }
    if (SUCCEEDED(hr) && out && *out) SpyOn(s_surfDescHooks, 6, *out, 12, (void*)Spy_SurfGetDesc);
}

static HRESULT STDMETHODCALLTYPE Hook_CreateRenderTarget(IDirect3DDevice9* dev, UINT w, UINT h, D3DFORMAT fmt, D3DMULTISAMPLE_TYPE ms,
                                                         DWORD q, BOOL lockable, IDirect3DSurface9** out, HANDLE* shared)
{
    HRESULT hr = s_origCreateRenderTarget(dev, w, h, fmt, ms, q, lockable, out, shared);
    LogSurfaceCreate("CreateRenderTarget", w, h, fmt, ms, hr, out, _ReturnAddress());
    return hr;
}

static HRESULT STDMETHODCALLTYPE Hook_CreateDepthStencil(IDirect3DDevice9* dev, UINT w, UINT h, D3DFORMAT fmt, D3DMULTISAMPLE_TYPE ms,
                                                         DWORD q, BOOL discard, IDirect3DSurface9** out, HANDLE* shared)
{
    HRESULT hr = s_origCreateDepthStencil(dev, w, h, fmt, ms, q, discard, out, shared);
    LogSurfaceCreate("CreateDepthStencilSurface", w, h, fmt, ms, hr, out, _ReturnAddress());
    return hr;
}

static bool FrameLog() { return s_dumping && s_frameLogs < 6000 && (s_frameLogs++, true); }

static HRESULT STDMETHODCALLTYPE Hook_SetViewport(IDirect3DDevice9* dev, const D3DVIEWPORT9* vp)
{
    D3DVIEWPORT9 scaled;
    if (vp && s_shadowRt) {
        LONG x0 = (LONG)vp->X, y0 = (LONG)vp->Y, x1 = (LONG)(vp->X + vp->Width), y1 = (LONG)(vp->Y + vp->Height);
        if (ScaleShadowRect(x0, y0, x1, y1)) {
            scaled = *vp;
            scaled.X = (DWORD)x0, scaled.Y = (DWORD)y0, scaled.Width = (DWORD)(x1 - x0), scaled.Height = (DWORD)(y1 - y0);
            vp = &scaled;
        }
    }
    static D3DVIEWPORT9 last;
    if (vp && s_dumping && (!s_viewportLogged || memcmp(vp, &last, sizeof(last))) && FrameLog()) {
        last = *vp;
        s_viewportLogged = true;
        Log("dump pass %02d: viewport %lu,%lu %lux%lu z %.3f-%.3f (after %lu draws)", s_pass, vp->X, vp->Y, vp->Width, vp->Height,
            vp->MinZ, vp->MaxZ, s_passDraws);
    }
    return s_origSetViewport(dev, vp);
}

static HRESULT STDMETHODCALLTYPE Hook_SetScissorRect(IDirect3DDevice9* dev, const RECT* r)
{
    RECT scaled;
    if (r && s_shadowRt) {
        scaled = *r;
        if (ScaleShadowRect(scaled.left, scaled.top, scaled.right, scaled.bottom)) r = &scaled;
    }
    static RECT last;
    if (r && s_dumping && (!s_scissorLogged || memcmp(r, &last, sizeof(last))) && FrameLog()) {
        last = *r;
        s_scissorLogged = true;
        Log("dump pass %02d: scissor %ld,%ld-%ld,%ld (after %lu draws)", s_pass, r->left, r->top, r->right, r->bottom, s_passDraws);
    }
    return s_origSetScissorRect(dev, r);
}

static HRESULT STDMETHODCALLTYPE Hook_SetDepthStencil(IDirect3DDevice9* dev, IDirect3DSurface9* s)
{
    if (FrameLog()) {
        char desc[128];
        DescribeSurface(s, desc);
        Log("dump pass %02d: depth stencil = %s (after %lu draws)", s_pass, desc, s_passDraws);
    }
    return s_origSetDepthStencil(dev, s);
}

static HRESULT STDMETHODCALLTYPE Hook_Clear(IDirect3DDevice9* dev, DWORD count, const D3DRECT* rects, DWORD flags, D3DCOLOR color, float z,
                                            DWORD stencil)
{
    D3DRECT scaled[16];
    if (s_shadowRt && count && rects && count <= 16) {
        bool all = true;
        for (DWORD i = 0; i < count && all; i++) {
            scaled[i] = rects[i];
            all = ScaleShadowRect(scaled[i].x1, scaled[i].y1, scaled[i].x2, scaled[i].y2);
        }
        if (all) rects = scaled;
    }
    if (FrameLog())
        Log("dump pass %02d: Clear flags %lx color %08lx z %.3f, %lu rects%s (%ld,%ld-%ld,%ld) (after %lu draws)", s_pass, flags, color, z,
            count, count > 1 ? ", first" : "", count && rects ? rects[0].x1 : -1, count && rects ? rects[0].y1 : -1,
            count && rects ? rects[0].x2 : -1, count && rects ? rects[0].y2 : -1, s_passDraws);
    return s_origClear(dev, count, rects, flags, color, z, stencil);
}

static void LogConstants(const char* what, const float* c, int rows)
{
    for (int r = 0; r < rows; r++) {
        const float* v = c + r * 4;
        if ((v[0] || v[1] || v[2] || v[3]) && s_constLines++ < 12000)
            Log("dump pass %02d:     %s c%d %g %g %g %g", s_pass, what, r, v[0], v[1], v[2], v[3]);
    }
}

static void LogShaderConstants(IDirect3DDevice9* dev, bool pixel, bool vertex)
{
    static float c[256 * 4];
    if (pixel) {
        if (SUCCEEDED(dev->GetPixelShaderConstantF(0, c, 224))) LogConstants("ps", c, 224);
        else Log("dump pass %02d:     pixel shader constants not readable", s_pass);
        int i[16 * 4];
        BOOL b[16];
        if (SUCCEEDED(dev->GetPixelShaderConstantI(0, i, 16)))
            for (int r = 0; r < 16; r++)
                if (i[r * 4] || i[r * 4 + 1] || i[r * 4 + 2] || i[r * 4 + 3])
                    Log("dump pass %02d:     ps i%d %d %d %d %d", s_pass, r, i[r * 4], i[r * 4 + 1], i[r * 4 + 2], i[r * 4 + 3]);
        if (SUCCEEDED(dev->GetPixelShaderConstantB(0, b, 16)))
            for (int r = 0; r < 16; r++)
                if (b[r]) Log("dump pass %02d:     ps b%d true", s_pass, r);
    }
    if (vertex && SUCCEEDED(dev->GetVertexShaderConstantF(0, c, 256))) LogConstants("vs", c, 256);
}

// the shader's bytecode into the dump folder; its constant table names the registers (fxc /dumpbin)
static void SaveShader(IUnknown* shader, bool pixel)
{
    if (!shader) return;
    UINT size = 0;
    HRESULT hr = pixel ? ((IDirect3DPixelShader9*)shader)->GetFunction(NULL, &size) : ((IDirect3DVertexShader9*)shader)->GetFunction(NULL, &size);
    if (FAILED(hr) || !size || size > (1u << 20)) return;
    void* code = HeapAlloc(GetProcessHeap(), 0, size);
    if (!code) return;
    hr = pixel ? ((IDirect3DPixelShader9*)shader)->GetFunction(code, &size) : ((IDirect3DVertexShader9*)shader)->GetFunction(code, &size);
    char path[MAX_PATH];
    wsprintfA(path, "%s\\f%lu_%s_%p.cso", s_dumpDir, s_dumpIndex, pixel ? "ps" : "vs", shader);
    HANDLE f = SUCCEEDED(hr) ? CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL) : INVALID_HANDLE_VALUE;
    if (f != INVALID_HANDLE_VALUE) {
        DWORD wrote = 0;
        WriteFile(f, code, size, &wrote, NULL);
        CloseHandle(f);
        Log("dump pass %02d:     %s shader %p saved (%u bytes)", s_pass, pixel ? "pixel" : "vertex", shader, size);
    }
    HeapFree(GetProcessHeap(), 0, code);
}

static bool ShadowSized(IDirect3DSurface9* s)
{
    D3DSURFACE_DESC d;
    return s && SUCCEEDED(s->GetDesc(&d)) && ShadowSize(d.Width, d.Height);
}

// a pass into a shadow-sized target or depth surface: the depth surface, the states a different map
// size would touch, the vertex shader and its constants (the light's projections)
static void ShadowPassStart(IDirect3DDevice9* dev)
{
    IDirect3DSurface9 *rt = NULL, *ds = NULL;
    dev->GetRenderTarget(0, &rt);
    dev->GetDepthStencilSurface(&ds);
    bool shadow = ShadowSized(rt) || ShadowSized(ds);
    char desc[128];
    DescribeSurface(ds, desc);
    if (rt) rt->Release();
    if (ds) ds->Release();
    DWORD bias = 0, slope = 0, cull = 0, write = 0, zfunc = 0, zwrite = 0, colorWrite = 0;
    dev->GetRenderState(D3DRS_DEPTHBIAS, &bias);
    dev->GetRenderState(D3DRS_SLOPESCALEDEPTHBIAS, &slope);
    dev->GetRenderState(D3DRS_CULLMODE, &cull);
    dev->GetRenderState(D3DRS_ZENABLE, &write);
    dev->GetRenderState(D3DRS_ZFUNC, &zfunc);
    dev->GetRenderState(D3DRS_ZWRITEENABLE, &zwrite);
    dev->GetRenderState(D3DRS_COLORWRITEENABLE, &colorWrite);
    Log("dump pass %02d:   depth stencil %s, depth bias %g slope %g, cull %lu, z %lu func %lu write %lu, colour write %lx", s_pass, desc,
        *(float*)&bias, *(float*)&slope, cull, write, zfunc, zwrite, colorWrite);
    if (!shadow) return;
    IDirect3DVertexShader9* vs = NULL;
    IDirect3DPixelShader9* ps = NULL;
    dev->GetVertexShader(&vs);
    dev->GetPixelShader(&ps);
    Log("dump pass %02d:   a shadow-sized pass: vertex shader %p, pixel shader %p", s_pass, vs, ps);
    LogShaderConstants(dev, false, true);
    SaveShader(vs, false);
    SaveShader(ps, true);
    if (vs) vs->Release();
    if (ps) ps->Release();
}

// a draw that reads a shadow map (a depth texture, or a render target of a shadow map's size): every such
// stage with its samplers, then the shader constants and both shaders, once per pixel shader in the dump
static void ShadowDrawCheck(IDirect3DDevice9* dev)
{
    if (s_constLoggedCount >= 24) return;
    IDirect3DPixelShader9* ps = NULL;
    dev->GetPixelShader(&ps);
    if (ps) ps->Release();
    for (int i = 0; i < s_constLoggedCount; i++)
        if (s_constLogged[i] == ps) return;
    int stages[16], count = 0;
    D3DSURFACE_DESC descs[16];
    for (DWORD stage = 0; stage < 16; stage++) {
        IDirect3DBaseTexture9* t = NULL;
        if (FAILED(dev->GetTexture(stage, &t)) || !t) continue;
        D3DSURFACE_DESC d = {};
        if (t->GetType() == D3DRTYPE_TEXTURE && SUCCEEDED(((IDirect3DTexture9*)t)->GetLevelDesc(0, &d)) &&
            ((d.Usage & D3DUSAGE_DEPTHSTENCIL) || ((d.Usage & D3DUSAGE_RENDERTARGET) && ShadowSize(d.Width, d.Height)))) {
            stages[count] = (int)stage;
            descs[count++] = d;
        }
        t->Release();
    }
    if (!count) return;
    s_constLogged[s_constLoggedCount++] = ps;
    D3DVIEWPORT9 vp = {};
    dev->GetViewport(&vp);
    Log("dump pass %02d: draw %lu reads %d shadow map candidates, pixel shader %p, viewport %lu,%lu %lux%lu", s_pass, s_passDraws, count, ps,
        vp.X, vp.Y, vp.Width, vp.Height);
    for (int k = 0; k < count; k++) {
        DWORD s = (DWORD)stages[k], minf = 0, magf = 0, mip = 0, u = 0, v = 0, border = 0, srgb = 0;
        dev->GetSamplerState(s, D3DSAMP_MINFILTER, &minf);
        dev->GetSamplerState(s, D3DSAMP_MAGFILTER, &magf);
        dev->GetSamplerState(s, D3DSAMP_MIPFILTER, &mip);
        dev->GetSamplerState(s, D3DSAMP_ADDRESSU, &u);
        dev->GetSamplerState(s, D3DSAMP_ADDRESSV, &v);
        dev->GetSamplerState(s, D3DSAMP_BORDERCOLOR, &border);
        dev->GetSamplerState(s, D3DSAMP_SRGBTEXTURE, &srgb);
        const D3DSURFACE_DESC& d = descs[k];
        Log("dump pass %02d:   stage %lu: %ux%u fmt %u usage %lx%s, filter %lu/%lu/%lu, address %lu/%lu, border %08lx, srgb %lu", s_pass, s,
            d.Width, d.Height, d.Format, d.Usage, ShadowSize(d.Width, d.Height) ? " (shadow size)" : "", minf, magf, mip, u, v, border,
            srgb);
    }
    LogShaderConstants(dev, true, true);
    IDirect3DVertexShader9* vs = NULL;
    dev->GetVertexShader(&vs);
    SaveShader(vs, false);
    if (vs) vs->Release();
    SaveShader(ps, true);
}

// F9 arms the dump; it starts once this Present has gone through, so the frame logged is the game's
// next one from its start, not what we and the overlays draw on top of this one (except SMAA's pass at
// Present, which still belongs to the picture of the dumped frame)
static bool s_dumpArmed;

static void DumpAtPresent(IDirect3DDevice9* dev)
{
    if (s_dumping) {
        EndPass(dev, "present");
        IDirect3DSurface9* bb = NULL;
        if (SUCCEEDED(dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb)) && bb) {
            DumpSurface(dev, bb, "backbuffer");
            bb->Release();
        }
        s_dumping = false;
        Log("dump %lu: done, %d passes, files in %s", s_dumpIndex, s_pass, s_dumpDir);
    }
    // F9 while the game has the focus dumps the next frame
    static bool was;
    DWORD pid = 0;
    GetWindowThreadProcessId(GetForegroundWindow(), &pid);
    bool down = pid == GetCurrentProcessId() && (GetAsyncKeyState(VK_F9) & 0x8000);
    if (down && !was && !s_dumpArmed && s_origSetRenderTarget) {
        s_dumpIndex++;
        ModulePath(s_dumpDir, "glimpse_dump");
        CreateDirectoryA(s_dumpDir, NULL);
        s_dumpArmed = true;
        s_pass = 0;
        s_passDraws = 0;
        s_frameLogs = 0;
        s_constLines = 0;
        s_viewportLogged = s_scissorLogged = false;
        s_constLoggedCount = 0;
        Log("dump %lu: next frame, graphics quality %d", s_dumpIndex, KnownBuild() ? *(int*)G(0x00D0F7E8) : -1);
    }
    was = down;
}

// SteamLAN: the banner's font lets go of its device resources around a Reset
typedef HRESULT(STDMETHODCALLTYPE* Reset_t)(IDirect3DDevice9*, D3DPRESENT_PARAMETERS*);
static Reset_t s_origReset;

// Another module may put its own hook into the device's vtable after ours: Steam's overlay does so once
// the game process calls SteamAPI_Init (SteamLAN), and Present then no longer reached us, so the frame
// pacing stopped and the game ran at half speed. CheckD3DHooks (every input frame) puts ours in front of
// such a hook again and passes on to it; if that hook calls ours in turn, the nested call goes straight
// to the device's own function (s_d3dPresent / s_d3dReset), so nothing runs twice and nothing loops.
static void** s_vt;
static Present_t s_d3dPresent;
static Reset_t s_d3dReset;
static __declspec(thread) int s_presentDepth, s_resetDepth;

// The slots are checked on the game's thread (CheckD3DHooks) and after a Reset on the render thread; one
// lock keeps the two apart, and while the runtime's Reset runs (and may rewrite slots) no check starts.
// The lock is never held across the runtime's Reset: that may wait for the game's window thread.
static CRITICAL_SECTION s_hookLock;
static volatile LONG s_inReset;
static void CheckSlotsLocked();

static HRESULT STDMETHODCALLTYPE Hook_Reset(IDirect3DDevice9* dev, D3DPRESENT_PARAMETERS* pp)
{
    if (s_resetDepth) return s_d3dReset(dev, pp);  // another hook called ours again
    s_resetDepth++;
    InterlockedIncrement(&s_inReset);
    EnterCriticalSection(&s_hookLock);  // a check under way ends first
    LeaveCriticalSection(&s_hookLock);
    OverlayDeviceLost();
    SmaaDeviceLost();
    HdrDeviceLost();
    UltrawideDeviceLost();
    HdrPresentParams(NULL, 0, NULL, pp);  // keeps the device windowed for the HDR swap chain
    HRESULT hr = s_origReset(dev, pp);
    ForgetStages();
    s_shadowRt = false;
    EnterCriticalSection(&s_hookLock);
    CheckSlotsLocked();  // the runtime may have written its functions back
    LeaveCriticalSection(&s_hookLock);
    InterlockedDecrement(&s_inReset);
    if (g_cfg.log) Log("Direct3D: Reset %ux%u windowed %d -> %08lX", pp ? pp->BackBufferWidth : 0, pp ? pp->BackBufferHeight : 0,
                       pp ? pp->Windowed : -1, (unsigned long)hr);
    if (SUCCEEDED(hr)) {
        OverlayDeviceReset();
        HdrDeviceReset(dev);
        if (g_cfg.ultrawideFix == 2) UltrawideDeviceReset(dev, pp);
    }
    s_resetDepth--;
    return hr;
}

static const char* ModuleOf(const void* p, char* buf, DWORD n)
{
    HMODULE mod = NULL;
    buf[0] = 0;
    if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           (LPCSTR)p, &mod) && mod && GetModuleFileNameA(mod, buf, n)) {
        const char* slash = strrchr(buf, '\\');
        return slash ? slash + 1 : buf;
    }
    return "?";
}

// the slot gets value only if it still holds expected
static bool SetSlot(int index, void* expected, void* value)
{
    DWORD old;
    VirtualProtect(&s_vt[index], sizeof(void*), PAGE_EXECUTE_READWRITE, &old);
    bool done = InterlockedCompareExchangePointer(&s_vt[index], value, expected) == expected;
    VirtualProtect(&s_vt[index], sizeof(void*), old, &old);
    return done;
}

// the runtime's own code (d3d9.dll), as opposed to another module's hook
static bool InRuntime(void* p)
{
    static HMODULE d3d9 = GetModuleHandleA("d3d9.dll");
    HMODULE m = NULL;
    return d3d9 &&
           GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, (LPCSTR)p, &m) &&
           m == d3d9;
}

static HRESULT STDMETHODCALLTYPE Diag_Present(IDirect3DDevice9* dev, const RECT* src, const RECT* dst, HWND wnd, const RGNDATA* dirty);

// The Direct3D runtime writes its own functions back into the device's vtable at times (seen right after
// the Reset at the start: from then on SetTexture, SetSamplerState and the draws went straight to d3d9.dll,
// so the anisotropic filtering, the texture detail and the frame dump never saw them). Every slot hooked
// here is checked each input frame and after a Reset; where the runtime's own function is back, ours goes
// in front of it again and calls on to that function (it may be another variant than the one noted at
// the start). A slot another module took is left to it: its hook may call through the vtable again.
static volatile LONG s_repatched;

static void RepatchSlots()
{
    static void* foreign[128];  // the last other module's hook seen in a slot, left to it
    static int logged;
    for (int i = 0; i < s_slotHookCount; i++) {
        SlotHook& h = s_slotHooks[i];
        void* cur = s_vt[h.index];
        if (cur == h.hook || cur == foreign[h.index] || !*h.orig) continue;
        if (!InRuntime(cur)) {
            char buf[MAX_PATH];
            Log("Direct3D: slot %d was taken over by %s, left to it", h.index, ModuleOf(cur, buf, sizeof(buf)));
            foreign[h.index] = cur;
            continue;
        }
        *h.orig = cur;  // before the slot changes, so a call meanwhile goes on to the runtime's function
        if (!SetSlot(h.index, cur, h.hook)) continue;  // changed meanwhile: the next check
        if (h.index == 37) s_shadowRt = false;  // render targets were set past the hook meanwhile
        InterlockedIncrement(&s_repatched);
        if (logged++ < 20) Log("Direct3D: slot %d holds the runtime's function again, our hook goes in front of it", h.index);
    }
}

static void CheckPresentReset();

static void CheckSlotsLocked()
{
    if (!s_vt) return;
    RepatchSlots();
    CheckPresentReset();
}

void CheckD3DHooks()
{
    if (!s_vt || s_inReset) return;
    EnterCriticalSection(&s_hookLock);
    if (!s_inReset) CheckSlotsLocked();
    LeaveCriticalSection(&s_hookLock);
}

static void CheckPresentReset()
{
    char buf[MAX_PATH];
    static int logged;  // the first few times; a module that keeps taking it back would flood the log
    if (s_d3dPresent && s_vt[17] != (void*)Diag_Present) {
        void* other = s_vt[17];
        if (logged++ < 5)
            Log("Direct3D: Present was taken over by %s, our hook goes in front of it again", ModuleOf(other, buf, sizeof(buf)));
        s_origPresent = (Present_t)other;
        if (InRuntime(other)) s_d3dPresent = (Present_t)other;  // the runtime's own variant now
        SetSlot(17, other, (void*)Diag_Present);
    }
    if (s_d3dReset && s_vt[16] != (void*)Hook_Reset) {
        void* other = s_vt[16];
        if (logged++ < 5)
            Log("Direct3D: Reset was taken over by %s, our hook goes in front of it again", ModuleOf(other, buf, sizeof(buf)));
        s_origReset = (Reset_t)other;
        if (InRuntime(other)) s_d3dReset = (Reset_t)other;
        SetSlot(16, other, (void*)Hook_Reset);
    }
}

volatile DWORD g_lastPresent;  // GetTickCount of the last Present (OnInputFrame notices when it stops)

static HRESULT STDMETHODCALLTYPE Diag_Present(IDirect3DDevice9* dev, const RECT* src, const RECT* dst, HWND wnd, const RGNDATA* dirty)
{
    if (s_presentDepth) return s_d3dPresent(dev, src, dst, wnd, dirty);  // another hook called ours again
    s_presentDepth++;
    struct Leave {
        ~Leave() { s_presentDepth--; }
    } leave;
    SmaaPresent(dev);  // before the frame dump, the banners and HDR read the back buffer
    if (g_cfg.log) DumpAtPresent(dev);
    static DWORD frames, since;
    DWORD now = GetTickCount();
    frames++;
    if (!since) since = now;
    if (now - since >= 1000) {
        if (TestFeed* feed = GetTestFeed()) feed->fps = frames * 1000.0f / (now - since);
        frames = 0;
        since = now;
    }
    if (TestFeed* feed = GetTestFeed()) {
        if (feed->shotRequest) {
            CaptureBackBuffer(dev, feed->shotRequest);
            feed->shotRequest = 0;
        }
    }
    if (g_cfg.ultrawideFix == 2) UltrawidePresent(dev);  // its last touches to the back buffer, before HDR reads it
    // the banners after the ultrawide pillars are cleared, so that they are not wiped off
    if (g_cfg.steamLan) OnlinePresent(dev);
    MoviePresent(dev);
    HRESULT hr;
    StageState stages[16];
    memcpy(stages, s_stages, sizeof(stages));
    if (!HdrPresent(dev, &hr)) hr = s_origPresent(dev, src, dst, wnd, dirty);
    memcpy(s_stages, stages, sizeof(stages));
    if (s_dumpArmed) {  // the dump begins with the game's next frame
        s_dumpArmed = false;
        s_dumping = true;
    }
    if (g_cfg.log) {  // the device lost and back, the game window in the foreground or not
        static HRESULT lastHr = D3D_OK;
        static int lastFg = -1;
        if ((hr == D3DERR_DEVICELOST) != (lastHr == D3DERR_DEVICELOST))
            Log("Direct3D: Present %s", hr == D3DERR_DEVICELOST ? "reports the device lost" : "works again");
        lastHr = hr;
        DWORD pid = 0;
        GetWindowThreadProcessId(GetForegroundWindow(), &pid);
        int fg = pid == GetCurrentProcessId();
        if (fg != lastFg && lastFg >= 0) Log("window: the game %s the foreground", fg ? "has" : "lost");
        lastFg = fg;
    }
    if (g_cfg.log) {
        static DWORD hookSince;
        DWORD t = GetTickCount();
        if (!hookSince) hookSince = t;
        if (t - hookSince >= 10000) {
            hookSince = t;
            void** vt = *(void***)dev;
            char a[MAX_PATH], b[MAX_PATH];
            Log("hooks: %ld draws, %ld SetTexture, %ld CreateTexture in 10 s, %ld slots hooked again; device vtable %s (draw slot in %s, "
                "SetTexture slot in %s)",
                InterlockedExchange(&s_drawsSeen, 0), InterlockedExchange(&s_setTextureSeen, 0), InterlockedExchange(&s_createSeen, 0),
                InterlockedExchange(&s_repatched, 0),
                vt == s_vt ? "as hooked" : "another one", ModuleOf(vt[82], a, sizeof(a)), ModuleOf(vt[65], b, sizeof(b)));
        }
    }
    g_lastPresent = GetTickCount();
    FramePace();
    return hr;
}

static HRESULT STDMETHODCALLTYPE Diag_CreateDevice(IDirect3D9* d3d, UINT adapter, D3DDEVTYPE type, HWND focus, DWORD flags,
                                                    D3DPRESENT_PARAMETERS* pp, IDirect3DDevice9** out)
{
    Log("CreateDevice adapter=%u type=%d hwnd=%p fg=%p flags=%lx %ux%u fmt=%d windowed=%d refresh=%u interval=%x",
        adapter, type, focus, GetForegroundWindow(), flags, pp ? pp->BackBufferWidth : 0, pp ? pp->BackBufferHeight : 0,
        pp ? pp->BackBufferFormat : 0, pp ? pp->Windowed : -1, pp ? pp->FullScreen_RefreshRateInHz : 0,
        pp ? pp->PresentationInterval : 0);
    HdrPresentParams(d3d, adapter, focus, pp);
    ApplyQualityCap();  // the renderer is set up at the capped level
    HRESULT hr = s_origCreateDevice(d3d, adapter, type, focus, flags, pp, out);
    Log("CreateDevice -> %08lx", hr);
    if (SUCCEEDED(hr) && out && *out) {
        Log("available texture memory %u MB", (*out)->GetAvailableTextureMem() >> 20);
        LogForeignModules("after device");
        void** vt = *(void***)*out;
        bool ultrawide = g_cfg.ultrawideFix == 2;
        if (g_cfg.log || g_cfg.frameRate == 60 || g_cfg.steamLan || g_cfg.hdr || ultrawide || g_cfg.cutsceneSkip ||
            g_cfg.smaa) {
            PatchVtable(vt, 17, (void*)Diag_Present, (void**)&s_origPresent);
            s_d3dPresent = s_origPresent;  // the device's own, for nested calls (CheckD3DHooks)
        }
        if (g_cfg.steamLan || g_cfg.hdr || ultrawide || g_cfg.cutsceneSkip || g_cfg.smaa || g_cfg.anisotropy > 1 ||
            g_cfg.textureDetail > 0 || g_cfg.shadowResolution > 1) {
            // the overlay's font, the SMAA and HDR targets, the ultrawide surfaces and aspect, the samplers
            PatchVtable(vt, 16, (void*)Hook_Reset, (void**)&s_origReset);
            s_d3dReset = s_origReset;
        }
        HdrDeviceCreated(*out);
        SmaaDeviceCreated(*out);
        s_device = *out;
        if (g_cfg.ultrawideFix == 2) UltrawideDeviceCreated(*out, pp, focus);
        HdrInstallUiHook();
        if (g_cfg.anisotropy > 1 || g_cfg.textureDetail > 0) {
            PatchVtable(vt, 65, (void*)Hook_SetTexture, (void**)&s_origSetTexture);
            if (g_cfg.anisotropy > 1) Log("anisotropic filtering %dx", g_cfg.anisotropy);
            if (g_cfg.textureDetail > 0) Log("texture detail: the top %d mip levels skipped", g_cfg.textureDetail);
        }
        PatchVtable(vt, 69, (void*)Hook_SetSamplerState, (void**)&s_origSetSamplerState);  // filtering, edge clamp
        PatchVtable(vt, 37, (void*)Hook_SetRenderTarget, (void**)&s_origSetRenderTarget);  // quad offset, frame dump
        if (g_cfg.log || g_cfg.shadowResolution > 1) {  // the shadow map's size, and the survey (Log=1)
            SurveyInit();
            ShadowFactorFor(*out);
            PatchVtable(vt, 23, (void*)Hook_CreateTexture, (void**)&s_origCreateTexture);
            PatchVtable(vt, 43, (void*)Hook_Clear, (void**)&s_origClear);
            PatchVtable(vt, 47, (void*)Hook_SetViewport, (void**)&s_origSetViewport);
            PatchVtable(vt, 75, (void*)Hook_SetScissorRect, (void**)&s_origSetScissorRect);
            if (s_shadowFactor > 1) Log("shadow maps: %ux resolution", s_shadowFactor);
        }
        if (g_cfg.log) {  // frame dump on F9
            PatchVtable(vt, 34, (void*)Hook_StretchRect, (void**)&s_origStretchRect);
            PatchVtable(vt, 81, (void*)Hook_DrawPrimitive, (void**)&s_origDrawPrimitive);
            PatchVtable(vt, 82, (void*)Hook_DrawIndexedPrimitive, (void**)&s_origDrawIndexedPrimitive);
            PatchVtable(vt, 83, (void*)Hook_DrawPrimitiveUP, (void**)&s_origDrawPrimitiveUP);
            PatchVtable(vt, 84, (void*)Hook_DrawIndexedPrimitiveUP, (void**)&s_origDrawIndexedPrimitiveUP);
            // shadow map survey: creation all along, the rest in the dumped frame
            PatchVtable(vt, 25, (void*)Hook_CreateCubeTexture, (void**)&s_origCreateCubeTexture);
            PatchVtable(vt, 28, (void*)Hook_CreateRenderTarget, (void**)&s_origCreateRenderTarget);
            PatchVtable(vt, 29, (void*)Hook_CreateDepthStencil, (void**)&s_origCreateDepthStencil);
            PatchVtable(vt, 39, (void*)Hook_SetDepthStencil, (void**)&s_origSetDepthStencil);
            Log("frame dump: F9");
        }
        s_vt = vt;  // the checks start once every slot is in place
    }
    return hr;
}

static IDirect3D9* WINAPI Diag_Direct3DCreate9(UINT sdk)
{
    // restore the prologue and call through; one create is all we want to see
    DWORD old;
    VirtualProtect((void*)s_origCreate9, 5, PAGE_EXECUTE_READWRITE, &old);
    memcpy((void*)s_origCreate9, s_create9Prologue, 5);
    VirtualProtect((void*)s_origCreate9, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)s_origCreate9, 5);

    // DumpTextures=1: the textures are written before the game makes its device, then the game ends
    if (g_cfg.dumpTextures) RunTextureDump();
    IDirect3D9* d3d = s_origCreate9(sdk);
    Log("Direct3DCreate9(%u) -> %p, %u adapters", sdk, d3d, d3d ? d3d->GetAdapterCount() : 0);
    if (d3d) {
        void** vt = *(void***)d3d;
        s_origCreateDevice = (CreateDevice_t)vt[16];
        VirtualProtect(&vt[16], sizeof(void*), PAGE_EXECUTE_READWRITE, &old);
        vt[16] = (void*)Diag_CreateDevice;
        VirtualProtect(&vt[16], sizeof(void*), old, &old);
        for (UINT a = 0; a < d3d->GetAdapterCount(); a++) {
            D3DADAPTER_IDENTIFIER9 id;
            D3DDISPLAYMODE dm;
            if (SUCCEEDED(d3d->GetAdapterIdentifier(a, 0, &id)) && SUCCEEDED(d3d->GetAdapterDisplayMode(a, &dm)))
                Log("  adapter %u: %s %ux%u@%u modes=%u, vendor %04lX device %04lX driver %u.%u.%u.%u", a, id.Description,
                    dm.Width, dm.Height, dm.RefreshRate, d3d->GetAdapterModeCount(a, D3DFMT_X8R8G8B8), id.VendorId,
                    id.DeviceId, HIWORD(id.DriverVersion.HighPart), LOWORD(id.DriverVersion.HighPart),
                    HIWORD(id.DriverVersion.LowPart), LOWORD(id.DriverVersion.LowPart));
        }
    }
    return d3d;
}

void InstallD3DHooks()
{
    HMODULE d3d9 = GetModuleHandleA("d3d9.dll");
    if (!d3d9) d3d9 = LoadLibraryA("d3d9.dll");
    s_origCreate9 = d3d9 ? (Direct3DCreate9_t)GetProcAddress(d3d9, "Direct3DCreate9") : NULL;
    if (!s_origCreate9) return;
    InitializeCriticalSection(&s_hookLock);
    memcpy(s_create9Prologue, (void*)s_origCreate9, 5);
    BYTE jmp[5] = { 0xE9 };
    *(DWORD*)(jmp + 1) = (DWORD)Diag_Direct3DCreate9 - ((DWORD)s_origCreate9 + 5);
    DWORD old;
    VirtualProtect((void*)s_origCreate9, 5, PAGE_EXECUTE_READWRITE, &old);
    memcpy((void*)s_origCreate9, jmp, 5);
    VirtualProtect((void*)s_origCreate9, 5, old, &old);
    FlushInstructionCache(GetCurrentProcess(), (void*)s_origCreate9, 5);
}
