// SMAA 1x for Direct3D 9 (vs_3_0 / ps_3_0), the three passes of the reference implementation in
// third_party/smaa/SMAA.hlsl (MIT license). Compiled by build.bat with fxc into build\smaa_*.h.
// c0 = (1/width, 1/height, width, height) of the picture, in both shader stages.

float4 rtMetrics : register(c0);
#define SMAA_RT_METRICS rtMetrics
#define SMAA_HLSL_3
#define SMAA_PRESET_HIGH
#include "../third_party/smaa/SMAA.hlsl"

sampler2D colorTex : register(s0);   // the picture (edge detection: gamma, blending: sRGB read)
sampler2D edgesTex : register(s1);
sampler2D areaTex : register(s2);    // A8L8
sampler2D searchTex : register(s3);  // L8
sampler2D blendTex : register(s4);

// Direct3D 9 places texel centres half a pixel off the pixel centres
float4 HalfPixel(float4 pos) { return float4(pos.xy + float2(-rtMetrics.x, rtMetrics.y) * pos.w, pos.zw); }

void EdgeVS(inout float4 pos : POSITION, inout float2 uv : TEXCOORD0, out float4 offset[3] : TEXCOORD1)
{
    pos = HalfPixel(pos);
    SMAAEdgeDetectionVS(uv, offset);
}

float4 EdgePS(float2 uv : TEXCOORD0, float4 offset[3] : TEXCOORD1) : COLOR
{
    return float4(SMAALumaEdgeDetectionPS(uv, offset, colorTex), 0.0, 0.0);
}

void WeightVS(inout float4 pos : POSITION, inout float2 uv : TEXCOORD0, out float2 pixcoord : TEXCOORD1,
              out float4 offset[3] : TEXCOORD2)
{
    pos = HalfPixel(pos);
    SMAABlendingWeightCalculationVS(uv, pixcoord, offset);
}

float4 WeightPS(float2 uv : TEXCOORD0, float2 pixcoord : TEXCOORD1, float4 offset[3] : TEXCOORD2) : COLOR
{
    return SMAABlendingWeightCalculationPS(uv, pixcoord, offset, edgesTex, areaTex, searchTex, 0.0);
}

void BlendVS(inout float4 pos : POSITION, inout float2 uv : TEXCOORD0, out float4 offset : TEXCOORD1)
{
    pos = HalfPixel(pos);
    SMAANeighborhoodBlendingVS(uv, offset);
}

float4 BlendPS(float2 uv : TEXCOORD0, float4 offset : TEXCOORD1) : COLOR
{
    return SMAANeighborhoodBlendingPS(uv, offset, colorTex, blendTex);
}
