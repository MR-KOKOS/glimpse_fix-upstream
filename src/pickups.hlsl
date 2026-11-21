// [DLC] Pickups=1 (dlcpickups.cpp): Deadline's green pickup boxes, drawn into the tone mapped picture with the
// scene's depth (test only, no depth write). One draw per box of a unit cube (24 corners, 4 per face).
//   c0..c3   the camera's view x projection matrix, as columns (clip = dot(c[i], float4(world, 1)))
//   c4       xyz the box's centre, w half its size in metres
//   c5..c7   the box's x, y and z axes in world space (rows 0..2 of the .pkps transform)
// Pixel: c0 face colour, c1 edge colour (rgb, a = opacity), c2.x edge width as a fraction of the face
float4 g_vp[4] : register(c0);
float4 g_box : register(c4);
float4 g_axis[3] : register(c5);

struct VsOut {
    float4 pos : POSITION;
    float2 uv : TEXCOORD0;
};

VsOut PickupVS(float4 corner : POSITION, float2 uv : TEXCOORD0)
{
    float3 w = g_box.xyz + g_box.w * (corner.x * g_axis[0].xyz + corner.y * g_axis[1].xyz + corner.z * g_axis[2].xyz);
    float4 p = float4(w, 1);
    VsOut o;
    o.pos = float4(dot(g_vp[0], p), dot(g_vp[1], p), dot(g_vp[2], p), dot(g_vp[3], p));
    o.uv = uv;
    return o;
}

float4 g_face : register(c0);
float4 g_edge : register(c1);
float4 g_width : register(c2);

float4 PickupPS(float2 uv : TEXCOORD0) : COLOR
{
    float2 d = abs(uv - 0.5) * 2;
    float e = max(d.x, d.y);
    float k = smoothstep(1 - g_width.x, 1 - g_width.x * 0.4, e);
    return lerp(g_face, g_edge, k);
}

// ---------------------------------------------------------------------------------------------------------------
// The Xbox box model (tools\dlc\mode\pickupmodel.py: GreenPickupBox, a hexagonal token, from the user's own DLC
// data), as the Xbox's shader 0x58f57228 shades it, approximated for the tone mapped picture: albedo = the icon
// (texture_1) blended with the slide show (texture_4, 7 frames of 4 x 2 cells at 10 fps) by the vertex's alpha3,
// lit by its emissive 1.0 plus the cube map's reflection with the cheap Fresnel term (1 - |n.v|); blended in linear
// light (gamma 2 as the Xbox G-buffer encodes) and written gamma 2 encoded.
//   c0..c3   the camera's view x projection matrix, as columns
//   c4       xyz the box's centre, w the model's scale
//   c5..c7   the box's x, y and z axes in world space (rows 0..2 of the .pkps transform)
//   c8       xyz the camera's position in the world
//   c9       xy the slide show frame's offset in the texture
// Pixel: c0 x emissive gain, y reflection gain, z opacity; c1 rgb tint (1 for the green boxes); c2 rgb added per
//        unit of blue, w how far the bright parts go to white (the Xbox's bloom and HDR make the blue token a light
//        cyan with a whitish icon in the game; our pass comes after them)
//        s0 icon, s1 cube map, s2 slide show
float4 g_eye : register(c8);
float4 g_slide : register(c9);

struct ModelOut {
    float4 pos : POSITION;
    float4 uv : TEXCOORD0;      // xy icon, zw slide show
    float3 nrm : TEXCOORD1;     // world
    float3 view : TEXCOORD2;    // world, from the surface to the eye
    float2 alpha : TEXCOORD3;   // alpha1 (icon), alpha3 (slide show)
};

ModelOut PickupModelVS(float3 p : POSITION, float3 n : NORMAL, float2 uv0 : TEXCOORD0, float2 uv2 : TEXCOORD1,
                       float2 alpha : TEXCOORD2)
{
    float3 w = g_box.xyz + g_box.w * (p.x * g_axis[0].xyz + p.y * g_axis[1].xyz + p.z * g_axis[2].xyz);
    float4 q = float4(w, 1);
    ModelOut o;
    o.pos = float4(dot(g_vp[0], q), dot(g_vp[1], q), dot(g_vp[2], q), dot(g_vp[3], q));
    o.uv = float4(uv0, uv2 + g_slide.xy);
    o.nrm = n.x * g_axis[0].xyz + n.y * g_axis[1].xyz + n.z * g_axis[2].xyz;
    o.view = g_eye.xyz - w;
    o.alpha = alpha;
    return o;
}

float4 g_look : register(c0);
float4 g_tint : register(c1);
float4 g_cyan : register(c2);
sampler2D s_icon : register(s0);
samplerCUBE s_cube : register(s1);
sampler2D s_slideTex : register(s2);

float4 PickupModelPS(ModelOut i) : COLOR
{
    float3 n = normalize(i.nrm);
    float3 v = normalize(i.view);
    float ndv = dot(n, v);
    clip(ndv);  // the token is convex: the faces turned away are left out (no depth write, any winding)
    float4 icon = tex2D(s_icon, i.uv.xy);
    float4 slide = tex2D(s_slideTex, i.uv.zw);
    float3 base = lerp(0.25, icon.rgb * icon.rgb, saturate(icon.a * i.alpha.x));
    float3 albedo = lerp(base, slide.rgb * slide.rgb, saturate(slide.a * i.alpha.y));
    float3 r = texCUBE(s_cube, reflect(-v, n)).rgb;
    float3 c = albedo * g_look.x + r * r * ((1 - ndv) * g_look.y);
    c += g_cyan.rgb * c.b;
    float wb = saturate(c.b);
    c = lerp(c, c.bbb, g_cyan.w * wb * wb) * g_tint.rgb;
    return float4(sqrt(saturate(c)), g_look.z);
}

// With HDR=1 (hdr.cpp): the same draws once more into the HDR gain target with gain 1, so the boxes stay at paper
// white like the HUD instead of taking the boost of the scene behind them. c3.x the box's opacity (written where
// it is at least one half; the token is convex, so its back faces cover no other pixels than its front ones)
float4 g_gainFade : register(c3);

float4 PickupGainPS() : COLOR
{
    clip(g_gainFade.x - 0.5);
    return float4(1, 1, 1, 1);
}
