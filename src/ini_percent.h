// Split/Second controller fix - shared integer percentage parser for INI values
#pragma once
#include <stdlib.h>
#include <string.h>

// A whole number in percent, base 10 ("080" is 80), optionally followed by " %"; anything else (empty,
// a word, a fraction, or a value >= 255 characters) returns def; clamped to lo..hi. Shared by
// dinput8.dll and glimpse_config.exe so both interpret OpponentEngineVolume identically.
static inline int ParsePercentValue(const char* s, int def, int lo, int hi)
{
    if (!s || strlen(s) >= 255) return def;
    char* end = NULL;
    long v = strtol(s, &end, 10);
    if (end == s) return def;
    while (*end == ' ' || *end == '\t') end++;
    if (*end == '%') end++;
    while (*end == ' ' || *end == '\t') end++;
    if (*end) return def;
    return v < lo ? lo : v > hi ? hi : (int)v;
}
