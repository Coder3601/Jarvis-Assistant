"""GLSL 3.30 core shaders for the holographic core.

All scene geometry is drawn into an HDR (RGBA16F) buffer with additive
blending, then a multi-level bloom chain and a cinematic composite pass
produce the final image.
"""

COMMON = """
float hash11(float n) { return fract(sin(n * 12.9898) * 43758.5453123); }
vec3 rotateAxis(vec3 p, vec3 axis, float ang) {
    float c = cos(ang), s = sin(ang);
    return p * c + cross(axis, p) * s + axis * dot(axis, p) * (1.0 - c);
}
"""

# ---------------------------------------------------------------------------
# Generic line / triangle shader. Vertex layout: pos(3) u(1) v(1) seed(1)
#   u    : parameter along the element (0..1) – drives travelling highlights
#   v    : brightness for lines, or across-width coordinate for bands
#   seed : random value per element – phase offsets, flicker, jitter
# ---------------------------------------------------------------------------
GEO_VS = """#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in float aU;
layout(location = 2) in float aV;
layout(location = 3) in float aSeed;
uniform mat4 uModel;
uniform mat4 uView;
uniform mat4 uProj;
uniform float uTime;
uniform float uJitter;
out float vU;
out float vV;
out float vSeed;
out float vDepth;
""" + COMMON + """
void main() {
    vec3 p = aPos;
    if (uJitter > 0.0) {
        // electrical arcs: re-randomise interior vertices many times a second
        float k = floor(uTime * 24.0);
        vec3 n = vec3(hash11(aSeed * 31.0 + aU * 97.0 + k),
                      hash11(aSeed * 17.0 + aU * 53.0 + k * 1.3),
                      hash11(aSeed * 11.0 + aU * 71.0 + k * 0.7)) - 0.5;
        p += n * uJitter * sin(3.14159265 * aU);
    }
    vec4 view = uView * uModel * vec4(p, 1.0);
    vDepth = -view.z;
    vU = aU; vV = aV; vSeed = aSeed;
    gl_Position = uProj * view;
}
"""

GEO_FS = """#version 330 core
in float vU;
in float vV;
in float vSeed;
in float vDepth;
uniform vec3 uColor;
uniform float uAlpha;
uniform float uTime;
uniform float uRepeat;     // highlights per element
uniform float uSpeed;      // highlight travel speed
uniform float uSeedPhase;  // how much the seed offsets the highlight
uniform float uWidth;      // highlight width
uniform float uBase;       // brightness outside highlight
uniform float uGain;       // highlight gain (0 disables highlights)
uniform float uFlicker;    // electrical flicker rate (0 disables)
uniform float uPlates;     // number of mechanical plates (0 disables)
uniform float uPulse;      // slow per-element breathing amount
uniform int   uVMode;      // 0: v = brightness, 1: v = across-band coordinate
uniform float uFadeNear;
uniform float uFadeFar;
out vec4 oColor;
""" + COMMON + """
void main() {
    float a = uAlpha;
    if (uVMode == 0) {
        a *= vV;
    } else {
        float e = abs(vV - 0.5) * 2.0;
        a *= 0.18 + 1.0 * pow(e, 7.0);
    }
    if (uGain > 0.0) {
        float x = fract(vU * uRepeat - uTime * uSpeed + vSeed * uSeedPhase);
        float d = (x - 0.5) / uWidth;
        a *= uBase + uGain * exp(-d * d);
    }
    if (uPlates > 0.0) {
        float s = fract(vU * uPlates);
        a *= smoothstep(0.0, 0.035, s) * smoothstep(1.0, 0.965, s);
        float id = floor(vU * uPlates);
        float blink = hash11(id * 7.13 + floor(uTime * 0.6 + hash11(id) * 9.0));
        a *= 0.5 + 0.45 * step(0.84, blink) + 0.25 * hash11(id * 3.7);
    }
    if (uFlicker > 0.0) {
        float f = hash11(floor(uTime * uFlicker) + vSeed * 113.0);
        a *= step(0.78, f) * 2.2;
    }
    if (uPulse > 0.0) {
        a *= 1.0 - uPulse + uPulse * (0.5 + 0.5 * sin(uTime * 1.1 + vSeed * 6.2831));
    }
    float dz = clamp((vDepth - uFadeNear) / (uFadeFar - uFadeNear), 0.0, 1.0);
    a *= mix(1.45, 0.28, dz);
    oColor = vec4(uColor * a, 1.0);
}
"""

# ---------------------------------------------------------------------------
# Particles. Layout: pos(3) seed(1) size(1) speed(1)
# ---------------------------------------------------------------------------
PTS_VS = """#version 330 core
layout(location = 0) in vec3 aPos;
layout(location = 1) in float aSeed;
layout(location = 2) in float aSize;
layout(location = 3) in float aSpeed;
uniform mat4 uModel;
uniform mat4 uView;
uniform mat4 uProj;
uniform float uTime;
uniform float uPixelScale;
uniform float uBreath;
uniform float uFadeNear;
uniform float uFadeFar;
uniform float uSwirl;
out float vAlpha;
""" + COMMON + """
void main() {
    vec3 axis = normalize(vec3(hash11(aSeed * 1.7) - 0.5,
                               1.6 + hash11(aSeed * 2.3),
                               hash11(aSeed * 3.1) - 0.5));
    vec3 p = rotateAxis(aPos, axis, uTime * aSpeed);
    // depth-based drift: inner particles swirl faster
    float r = length(p);
    p = rotateAxis(p, vec3(0.0, 1.0, 0.0), uSwirl * uTime / max(r, 0.25));
    p *= 1.0 + uBreath * 0.035 * sin(uTime * 0.9 + aSeed * 40.0);
    vec4 view = uView * uModel * vec4(p, 1.0);
    gl_Position = uProj * view;
    float depth = max(-view.z, 0.1);
    gl_PointSize = clamp(aSize * uPixelScale / depth, 1.0, 64.0);
    float tw = 0.45 + 0.55 * pow(0.5 + 0.5 * sin(uTime * (1.2 + hash11(aSeed) * 4.0) + aSeed * 60.0), 2.0);
    float dz = clamp((depth - uFadeNear) / (uFadeFar - uFadeNear), 0.0, 1.0);
    vAlpha = tw * mix(1.5, 0.22, dz);
}
"""

PTS_FS = """#version 330 core
in float vAlpha;
uniform vec3 uColor;
uniform float uAlpha;
uniform vec3 uWhite;
out vec4 oColor;
void main() {
    vec2 c = gl_PointCoord * 2.0 - 1.0;
    float r2 = dot(c, c);
    if (r2 > 1.0) discard;
    float a = exp(-r2 * 6.0) + 0.25 * exp(-r2 * 2.0);
    vec3 col = mix(uColor, uWhite, exp(-r2 * 18.0) * 0.6);
    oColor = vec4(col * a * vAlpha * uAlpha, 1.0);
}
"""

# ---------------------------------------------------------------------------
# Camera-facing glow billboards (reactor light, lens streaks)
# ---------------------------------------------------------------------------
GLOW_VS = """#version 330 core
layout(location = 0) in vec2 aCorner;
uniform mat4 uModel;
uniform mat4 uView;
uniform mat4 uProj;
uniform vec2 uSize;
out vec2 vCoord;
void main() {
    vec4 c = uView * uModel * vec4(0.0, 0.0, 0.0, 1.0);
    c.xy += aCorner * uSize;
    vCoord = aCorner;
    gl_Position = uProj * c;
}
"""

GLOW_FS = """#version 330 core
in vec2 vCoord;
uniform vec3 uColor;
uniform float uCore;      // hot core gain
uniform float uSharp;     // core falloff
uniform float uHalo;      // halo gain
uniform float uHaloFall;  // halo falloff
uniform float uRings;     // faint interference rings
uniform float uTime;
uniform vec3 uWhite;
out vec4 oColor;
void main() {
    float r = length(vCoord);
    if (r > 1.0) discard;
    float edge = 1.0 - smoothstep(0.75, 1.0, r);
    float core = exp(-r * r * uSharp) * uCore;
    float halo = exp(-r * uHaloFall) * uHalo;
    float rings = uRings * (0.5 + 0.5 * sin(r * 70.0 - uTime * 3.0)) * exp(-r * 4.0);
    vec3 col = uColor * (halo + rings) + mix(uColor, uWhite, 0.75) * core;
    oColor = vec4(col * edge, 1.0);
}
"""

# ---------------------------------------------------------------------------
# Post processing
# ---------------------------------------------------------------------------
QUAD_VS = """#version 330 core
out vec2 vUV;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    vUV = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

BRIGHT_FS = """#version 330 core
in vec2 vUV;
uniform sampler2D uTex;
uniform vec2 uTexel;
uniform float uThreshold;
out vec4 oColor;
void main() {
    // 4-tap box filter while downsampling
    vec3 c = texture(uTex, vUV + uTexel * vec2(-0.5, -0.5)).rgb
           + texture(uTex, vUV + uTexel * vec2( 0.5, -0.5)).rgb
           + texture(uTex, vUV + uTexel * vec2(-0.5,  0.5)).rgb
           + texture(uTex, vUV + uTexel * vec2( 0.5,  0.5)).rgb;
    c *= 0.25;
    // guard against fp16 overflow / NaN fireflies from drivers
    if (any(isnan(c)) || any(isinf(c))) c = vec3(0.0);
    c = min(c, vec3(10.0));
    float b = max(c.r, max(c.g, c.b));
    float soft = clamp(b - uThreshold + 0.25, 0.0, 0.5);
    soft = soft * soft / 0.5;
    float k = max(soft, b - uThreshold) / max(b, 1e-4);
    oColor = vec4(c * k, 1.0);
}
"""

DOWN_FS = """#version 330 core
in vec2 vUV;
uniform sampler2D uTex;
uniform vec2 uTexel;
out vec4 oColor;
void main() {
    vec3 c = texture(uTex, vUV + uTexel * vec2(-1.0, -1.0)).rgb
           + texture(uTex, vUV + uTexel * vec2( 1.0, -1.0)).rgb
           + texture(uTex, vUV + uTexel * vec2(-1.0,  1.0)).rgb
           + texture(uTex, vUV + uTexel * vec2( 1.0,  1.0)).rgb;
    oColor = vec4(c * 0.25, 1.0);
}
"""

BLUR_FS = """#version 330 core
in vec2 vUV;
uniform sampler2D uTex;
uniform vec2 uDir;   // texel step * direction
out vec4 oColor;
void main() {
    float w[5] = float[](0.227027, 0.1945946, 0.1216216, 0.054054, 0.016216);
    vec3 c = texture(uTex, vUV).rgb * w[0];
    for (int i = 1; i < 5; ++i) {
        c += texture(uTex, vUV + uDir * float(i) * 1.5).rgb * w[i];
        c += texture(uTex, vUV - uDir * float(i) * 1.5).rgb * w[i];
    }
    oColor = vec4(c, 1.0);
}
"""

COMPOSITE_FS = """#version 330 core
in vec2 vUV;
uniform sampler2D uScene;
uniform sampler2D uBloom0;
uniform sampler2D uBloom1;
uniform sampler2D uBloom2;
uniform float uBloomStrength;
uniform float uExposure;
uniform float uTime;
uniform vec2 uResolution;
uniform vec3 uTint;
uniform int uTransparent;   // 1 = floating mini hologram: premultiplied alpha, no background
uniform vec3 uBgBase;       // background colours (per persona theme)
uniform vec3 uBgHaze;
uniform vec3 uBgGlow;
uniform vec3 uGridCol;
uniform vec3 uGrade;        // per-channel gamma: warm or cool highlights
out vec4 oColor;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
void main() {
    vec2 uv = vUV;
    float aspect = uResolution.x / uResolution.y;
    vec2 q = (uv - 0.5) * vec2(aspect, 1.0);
    float r = length(q);

    // subtle chromatic split towards the edges (projector lens feel)
    vec2 off = uTransparent == 1 ? vec2(0.0) : (uv - 0.5) * 0.0012;
    vec3 scene;
    scene.r = texture(uScene, uv + off).r;
    scene.g = texture(uScene, uv).g;
    scene.b = texture(uScene, uv - off).b;
    if (any(isnan(scene)) || any(isinf(scene))) scene = vec3(0.0);
    scene = min(scene, vec3(10.0));

    vec3 bloom = texture(uBloom0, uv).rgb * 0.9
               + texture(uBloom1, uv).rgb * 0.8
               + texture(uBloom2, uv).rgb * 0.9;

    // dark cinematic background with a warm atmospheric haze behind the core
    vec3 bg = uBgBase;
    bg += uBgHaze * exp(-r * r * 5.0);
    bg += uBgGlow * exp(-r * 1.6);
    // faint holographic floor grid
    vec2 g = abs(fract(q * 14.0) - 0.5);
    float grid = (1.0 - smoothstep(0.0, 0.03, min(g.x, g.y))) * 0.010 * exp(-r * 2.0);
    bg += uGridCol * grid;

    vec3 col = scene + bloom * uBloomStrength;
    col *= uTint;
    col = 1.0 - exp(-col * uExposure);          // filmic exposure curve
    col = pow(col, uGrade);                       // push highlights warm / cool

    if (uTransparent == 1) {
        // light becomes coverage: bright = opaque, black = fully see-through; soft circular edge
        col *= 1.0 - smoothstep(0.40, 0.50, r);
        float a = clamp(max(col.r, max(col.g, col.b)) * 1.3, 0.0, 1.0);
        oColor = vec4(min(col, vec3(a)), a);
        return;
    }
    col += bg;

    // scanlines + grain + vignette
    col *= 0.965 + 0.035 * sin(gl_FragCoord.y * 3.14159 * 0.66 + uTime * 2.0);
    col += (hash(gl_FragCoord.xy + fract(uTime) * 100.0) - 0.5) * 0.018;
    col *= 1.0 - smoothstep(0.55, 1.25, r) * 0.75;

    oColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
"""

# ---------------------------------------------------------------------------
# Glowing "atom" orbs (camera-facing quads). Layout: centre(3) corner(2) seed(1) size(1)
# ---------------------------------------------------------------------------
ORB_VS = """#version 330 core
layout(location = 0) in vec3 aCenter;
layout(location = 1) in vec2 aCorner;
layout(location = 2) in float aSeed;
layout(location = 3) in float aSize;
uniform mat4 uModel;
uniform mat4 uView;
uniform mat4 uProj;
uniform float uTime;
uniform float uPulse;
out vec2 vCoord;
out float vSeed;
out float vDepth;
void main() {
    vec4 c = uView * uModel * vec4(aCenter, 1.0);
    float pulse = 1.0 + 0.08 * sin(uTime * (1.3 + aSeed * 2.0) + aSeed * 40.0) + uPulse;
    c.xy += aCorner * aSize * pulse;
    vCoord = aCorner;
    vSeed = aSeed;
    vDepth = -c.z;
    gl_Position = uProj * c;
}
"""

ORB_FS = """#version 330 core
in vec2 vCoord;
in float vSeed;
in float vDepth;
uniform vec3 uColor;
uniform vec3 uWhite;
uniform float uAlpha;
uniform float uTime;
uniform float uFadeNear;
uniform float uFadeFar;
uniform float uRing;      // strength of the outer tick ring
out vec4 oColor;
void main() {
    float r = length(vCoord);
    if (r > 1.0) discard;
    float ang = atan(vCoord.y, vCoord.x);
    float core = exp(-r * r * 140.0) * 2.6;
    float inner = exp(-r * r * 22.0) * 0.9;
    float halo = exp(-r * 5.0) * 0.35;
    // fuzzy radial filaments
    float spikes = pow(abs(sin(ang * 23.0 + vSeed * 31.0)), 18.0) + pow(abs(sin(ang * 37.0 - vSeed * 17.0)), 26.0);
    spikes *= smoothstep(0.62, 0.18, r) * smoothstep(0.05, 0.16, r) * 0.55;
    // thin rotating ring with ticks
    float ringd = (r - 0.66) / 0.018;
    float ring = exp(-ringd * ringd);
    float ticks = step(0.55, fract((ang / 6.28318) * 48.0 + uTime * 0.05 * (vSeed - 0.5)));
    float arc = smoothstep(0.0, 0.2, sin(ang * 2.0 + uTime * (0.4 + vSeed) + vSeed * 9.0));
    float tring = exp(-pow((r - 0.76) / 0.03, 2.0)) * ticks * 0.5;
    float rings = (ring * (0.35 + 0.65 * arc) + tring) * uRing;
    float edge = 1.0 - smoothstep(0.85, 1.0, r);
    float dz = clamp((vDepth - uFadeNear) / (uFadeFar - uFadeNear), 0.0, 1.0);
    float fade = mix(1.4, 0.3, dz);
    vec3 col = uColor * (halo + inner + spikes + rings) + mix(uColor, uWhite, 0.7) * core;
    oColor = vec4(col * edge * fade * uAlpha, 1.0);
}
"""
