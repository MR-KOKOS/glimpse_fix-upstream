// Text panels drawn on the back buffer right before Present: the online banner (online.cpp) and the
// movie skip prompt (game.cpp). A dark panel behind the lines keeps them readable over any picture;
// the device state around it is captured and put back, so the game's next frame is not touched.
#include "common.h"
#include <d3d9.h>
#include <string.h>

typedef HRESULT(WINAPI* D3DXCreateFontA_t)(IDirect3DDevice9*, INT, UINT, UINT, UINT, BOOL, DWORD, DWORD, DWORD, DWORD, LPCSTR,
                                           void**);
static void* s_font;  // ID3DXFont: [2] Release, [15] DrawTextW, [16] OnLostDevice, [17] OnResetDevice
static IDirect3DStateBlock9* s_state;  // the device state around the panel; released before a Reset
static UINT s_fontForHeight;

static void FontCall(int slot)
{
    if (s_font) ((HRESULT(WINAPI*)(void*))(*(void***)s_font)[slot])(s_font);
}

void OverlayDeviceLost()
{
    FontCall(16);
    if (s_state) s_state->Release();
    s_state = NULL;
}
void OverlayDeviceReset() { FontCall(17); }

static D3DCOLOR Faded(D3DCOLOR c, float alpha)
{
    unsigned a = (unsigned)((c >> 24) * alpha + 0.5f);
    return (c & 0x00FFFFFF) | (a > 255 ? 255 : a) << 24;
}

void OverlayDraw(IDirect3DDevice9* dev, const char* const* lines, const DWORD* colors, int count, int corner, float alpha)
{
    if (!dev || count <= 0 || alpha <= 0) return;
    if (count > 4) count = 4;
    if (alpha > 1) alpha = 1;
    // the game's own texts and Steam's lobby names are UTF-8: drawn as UTF-16, so that a Russian
    // name shows on a German Windows too (the Windows code page would turn it into "?")
    WCHAR wide[4][192];
    for (int i = 0; i < count; i++)
        if (!MultiByteToWideChar(CP_UTF8, 0, lines[i], -1, wide[i], 192)) wide[i][0] = 0;

    // the game may leave another render target set when it presents: draw on the back buffer and put
    // the game's target and viewport back afterwards
    static bool reported;
    IDirect3DSurface9 *rt = NULL, *bb = NULL;
    dev->GetRenderTarget(0, &rt);
    if (FAILED(dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb)) || !bb) {
        if (rt) rt->Release();
        if (!reported) Log("overlay: no back buffer");
        reported = true;
        return;
    }
    D3DSURFACE_DESC desc = {};
    bb->GetDesc(&desc);
    bool switched = rt != bb;
    D3DVIEWPORT9 vp = {};
    dev->GetViewport(&vp);
    if (switched) dev->SetRenderTarget(0, bb);
    struct Restore {
        IDirect3DDevice9* dev;
        IDirect3DSurface9 *rt, *bb;
        bool switched;
        D3DVIEWPORT9* vp;
        ~Restore()
        {
            if (switched && rt) dev->SetRenderTarget(0, rt);
            dev->SetViewport(vp);
            if (rt) rt->Release();
            bb->Release();
        }
    } restore = { dev, rt, bb, switched, &vp };
    if (!desc.Height) return;

    UINT height = desc.Height / 40 < 16 ? 16 : desc.Height / 40;
    if (s_font && s_fontForHeight != height) {
        ((ULONG(WINAPI*)(void*))(*(void***)s_font)[2])(s_font);
        s_font = NULL;
    }
    if (!s_font) {
        HMODULE x = GetModuleHandleA("d3dx9_42.dll");
        D3DXCreateFontA_t create = x ? (D3DXCreateFontA_t)GetProcAddress(x, "D3DXCreateFontA") : NULL;
        if (!create || FAILED(create(dev, (INT)height, 0, FW_BOLD, 1, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                                     ANTIALIASED_QUALITY, DEFAULT_PITCH | FF_DONTCARE, "Arial", &s_font))) {
            static bool logged;
            if (!logged) Log("overlay: no font (d3dx9_42.dll)");
            logged = true;
            s_font = NULL;
            return;
        }
        s_fontForHeight = height;
    }
    typedef INT(WINAPI* DrawTextW_t)(void*, void*, LPCWSTR, INT, RECT*, DWORD, D3DCOLOR);
    DrawTextW_t draw = (DrawTextW_t)(*(void***)s_font)[15];
    D3DVIEWPORT9 full = { 0, 0, desc.Width, desc.Height, 0.0f, 1.0f };
    dev->SetViewport(&full);
    HRESULT begun = dev->BeginScene();
    if (!reported) {
        Log("overlay: drawn %ux%u, the game's render target %s the back buffer, BeginScene %08lX", desc.Width,
            desc.Height, switched ? "was not" : "was", (unsigned long)begun);
        reported = true;
    }
    if (FAILED(begun)) return;

    int width = 0;
    for (int i = 0; i < count; i++) {
        RECT m = { 0, 0, 0, 0 };
        draw(s_font, NULL, wide[i], -1, &m, DT_CALCRECT | DT_LEFT | DT_TOP | DT_SINGLELINE, 0xFFFFFFFF);
        if (m.right > width) width = m.right;
    }
    int pad = (int)height / 2, lineStep = (int)(height * 1.25f), margin = (int)desc.Height / 30;
    int blockHeight = (count - 1) * lineStep + (int)height;
    int x = corner == OVERLAY_BOTTOM_RIGHT ? (int)desc.Width - margin - width : margin;
    int y = corner == OVERLAY_BOTTOM_RIGHT ? (int)desc.Height - margin - blockHeight : margin;
    float l = (float)(x - pad), t = (float)(y - pad), r_ = (float)(x + width + pad), b = (float)(y + blockHeight + pad);
    if (!s_state) dev->CreateStateBlock(D3DSBT_ALL, &s_state);
    IDirect3DStateBlock9* saved = s_state;
    if (saved && SUCCEEDED(saved->Capture())) {
        SaveStage0Tracking();
        struct V { float x, y, z, w; D3DCOLOR c; };
        const D3DCOLOR panel = Faded(D3DCOLOR_ARGB(200, 12, 12, 16), alpha);
        V quad[4] = { { l - 0.5f, t - 0.5f, 0, 1, panel }, { r_ - 0.5f, t - 0.5f, 0, 1, panel },
                      { l - 0.5f, b - 0.5f, 0, 1, panel }, { r_ - 0.5f, b - 0.5f, 0, 1, panel } };
        dev->SetPixelShader(NULL);
        dev->SetVertexShader(NULL);
        dev->SetTexture(0, NULL);
        dev->SetFVF(D3DFVF_XYZRHW | D3DFVF_DIFFUSE);
        dev->SetRenderState(D3DRS_ZENABLE, FALSE);
        dev->SetRenderState(D3DRS_ZWRITEENABLE, FALSE);
        dev->SetRenderState(D3DRS_STENCILENABLE, FALSE);
        dev->SetRenderState(D3DRS_CULLMODE, D3DCULL_NONE);
        dev->SetRenderState(D3DRS_LIGHTING, FALSE);
        dev->SetRenderState(D3DRS_FOGENABLE, FALSE);
        dev->SetRenderState(D3DRS_ALPHATESTENABLE, FALSE);
        dev->SetRenderState(D3DRS_SCISSORTESTENABLE, FALSE);
        dev->SetRenderState(D3DRS_SRGBWRITEENABLE, FALSE);
        dev->SetRenderState(D3DRS_COLORWRITEENABLE, 0xF);
        dev->SetRenderState(D3DRS_ALPHABLENDENABLE, TRUE);
        dev->SetRenderState(D3DRS_SRCBLEND, D3DBLEND_SRCALPHA);
        dev->SetRenderState(D3DRS_DESTBLEND, D3DBLEND_INVSRCALPHA);
        dev->SetRenderState(D3DRS_BLENDOP, D3DBLENDOP_ADD);
        dev->SetTextureStageState(0, D3DTSS_COLOROP, D3DTOP_SELECTARG1);
        dev->SetTextureStageState(0, D3DTSS_COLORARG1, D3DTA_DIFFUSE);
        dev->SetTextureStageState(0, D3DTSS_ALPHAOP, D3DTOP_SELECTARG1);
        dev->SetTextureStageState(0, D3DTSS_ALPHAARG1, D3DTA_DIFFUSE);
        dev->SetTextureStageState(1, D3DTSS_COLOROP, D3DTOP_DISABLE);
        dev->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, quad, sizeof(quad[0]));
        saved->Apply();
        RestoreStage0Tracking();
    }
    for (int i = 0; i < count; i++) {
        RECT shadow = { x + 2, y + 2, (LONG)desc.Width, y + (int)height * 2 };
        RECT r = { x, y, (LONG)desc.Width, y + (int)height * 2 };
        draw(s_font, NULL, wide[i], -1, &shadow, DT_NOCLIP | DT_LEFT | DT_TOP, Faded(D3DCOLOR_ARGB(200, 0, 0, 0), alpha));
        draw(s_font, NULL, wide[i], -1, &r, DT_NOCLIP | DT_LEFT | DT_TOP, Faded(colors[i], alpha));
        y += lineStep;
    }
    dev->EndScene();
}
