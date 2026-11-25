// Split/Second PC fix - shared HUD resolution parser for the runtime and settings tool
#pragma once
#include <errno.h>
#include <stdlib.h>

static inline int ParseHudResolution(const char* value, bool* recognized = NULL)
{
    while (*value == ' ' || *value == '\t') value++;
    if (recognized) *recognized = true;
    if (!*value || *value == ';' || *value == '#') return 0;

    errno = 0;
    char* end = NULL;
    long size = strtol(value, &end, 10);
    if (end != value && errno != ERANGE) {
        while (*end == ' ' || *end == '\t') end++;
        if ((!*end || *end == ';' || *end == '#') &&
            (size == -1 || size == 0 || size == 1 || size == 2 || size == 4))
            return (int)size;
    }
    if (recognized) *recognized = false;
    return 0;
}
