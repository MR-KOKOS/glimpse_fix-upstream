/* Offline check of converted banks with the PC game's own FMOD Ex DLLs (no sound output, no game code).
 *
 *   fmodcheck <dll dir> fsb <bank.fsb> [dump dir]     open as compressed sample, sample and stream; list
 *                                                      subsounds; optionally dump the stream decode as raw
 *                                                      s16 (dump dir/NNN.raw)
 *   fmodcheck <dll dir> fev <media dir> <name.fev>    load the event file, get (= load the waves of) every
 *                                                      event by project id, start/update/stop each
 *   fmodcheck <dll dir> fevprops <media dir> <name.fev>  list every property of every event (info only)
 * Build (x86): build_fmodcheck.bat
 */
#include <windows.h>
#include <stdio.h>
#include <string.h>

typedef int R;
#define API __stdcall
typedef R (API *tSysCreate)(void **);
typedef R (API *tSysSetOutput)(void *, int);
typedef R (API *tSysInit)(void *, int, unsigned, void *);
typedef R (API *tSysGetVersion)(void *, unsigned *);
typedef R (API *tSysCreateSound)(void *, const char *, unsigned, void *, void **);
typedef R (API *tSysUpdate)(void *);
typedef R (API *tSndNumSub)(void *, int *);
typedef R (API *tSndSub)(void *, int, void **);
typedef R (API *tSndName)(void *, char *, int);
typedef R (API *tSndFormat)(void *, int *, int *, int *, int *);
typedef R (API *tSndLength)(void *, unsigned *, unsigned);
typedef R (API *tSndLoop)(void *, unsigned *, unsigned, unsigned *, unsigned);
typedef R (API *tSndDefaults)(void *, float *, float *, float *, int *);
typedef R (API *tSndRead)(void *, void *, unsigned, unsigned *);
typedef R (API *tSndSeek)(void *, unsigned);
typedef R (API *tSndRelease)(void *);
typedef R (API *tEsCreate)(void **);
typedef R (API *tEsSys)(void *, void **);
typedef R (API *tEsInit)(void *, int, unsigned, void *, unsigned);
typedef R (API *tEsMedia)(void *, const char *);
typedef R (API *tEsLoad)(void *, const char *, void *, void **);
typedef R (API *tEsUpdate)(void *);
typedef R (API *tEsVersion)(void *, unsigned *);
typedef R (API *tPrjNum)(void *, int *);
typedef R (API *tPrjGet)(void *, int, unsigned, void **);
typedef R (API *tEvInfo)(void *, int *, char **, void *);
typedef R (API *tEvStart)(void *);
typedef R (API *tEvStop)(void *, int);
typedef R (API *tEvNumProp)(void *, int *);
typedef R (API *tEvPropInfo)(void *, int *, char **, int *);
typedef R (API *tEvPropGet)(void *, int, void *, int);

#define FMOD_SOFTWARE 0x40
#define FMOD_CREATESTREAM 0x80
#define FMOD_CREATESAMPLE 0x100
#define FMOD_CREATECOMPRESSEDSAMPLE 0x200
#define FMOD_TIMEUNIT_PCM 2
#define OUT_NOSOUND 2

static HMODULE ex, ev;
static void *P(HMODULE m, const char *n)
{
    void *f = (void *)GetProcAddress(m, n);
    if (!f) { printf("missing export %s\n", n); exit(3); }
    return f;
}

static int check_fsb(const char *fn, const char *dump)
{
    tSysCreate sc = P(ex, "_FMOD_System_Create@4");
    tSysSetOutput so = P(ex, "_FMOD_System_SetOutput@8");
    tSysInit si = P(ex, "_FMOD_System_Init@16");
    tSysGetVersion sv = P(ex, "_FMOD_System_GetVersion@8");
    tSysCreateSound cs = P(ex, "_FMOD_System_CreateSound@20");
    tSndNumSub nsub = P(ex, "_FMOD_Sound_GetNumSubSounds@8");
    tSndSub gsub = P(ex, "_FMOD_Sound_GetSubSound@12");
    tSndName gname = P(ex, "_FMOD_Sound_GetName@12");
    tSndFormat gfmt = P(ex, "_FMOD_Sound_GetFormat@20");
    tSndLength glen = P(ex, "_FMOD_Sound_GetLength@12");
    tSndLoop gloop = P(ex, "_FMOD_Sound_GetLoopPoints@20");
    tSndDefaults gdef = P(ex, "_FMOD_Sound_GetDefaults@20");
    tSndRead rd = P(ex, "_FMOD_Sound_ReadData@16");
    tSndSeek sk = P(ex, "_FMOD_Sound_SeekData@8");
    tSndRelease rel = P(ex, "_FMOD_Sound_Release@4");
    void *sys = 0, *snd = 0;
    unsigned ver = 0;
    int r, n = 0, i, errors = 0, pass;
    static const unsigned modes[3] = {FMOD_SOFTWARE | FMOD_CREATECOMPRESSEDSAMPLE, FMOD_SOFTWARE | FMOD_CREATESAMPLE,
                                      FMOD_SOFTWARE | FMOD_CREATESTREAM};
    static const char *mname[3] = {"compressed sample", "sample", "stream"};
    if ((r = sc(&sys)) || (r = so(sys, OUT_NOSOUND)) || (r = si(sys, 32, 0, 0))) { printf("init error %d\n", r); return 2; }
    sv(sys, &ver);
    printf("FMOD Ex %08X\n", ver);
    setvbuf(stdout, 0, _IONBF, 0);
    /* listing from the in-memory sample pass; the stream pass (only with a dump dir) decodes */
    for (pass = 0; pass < (dump ? 3 : 2); pass++) {
        r = cs(sys, fn, modes[pass], 0, &snd);
        if (r) { printf("%s: createSound error %d\n", mname[pass], r); errors++; continue; }
        nsub(snd, &n);
        printf("%s: ok, %d subsounds\n", mname[pass], n);
        for (i = 0; i < n; i++) {
            void *sub = 0;
            char name[256] = "";
            int type = 0, fmt = 0, ch = 0, bits = 0, pri = 0;
            unsigned len = 0, ls = 0, le = 0;
            float fr = 0, vol = 0, pan = 0;
            r = gsub(snd, i, &sub);
            if (r || !sub) { printf("  %3d getSubSound error %d\n", i, r); errors++; continue; }
            gname(sub, name, sizeof name);
            gfmt(sub, &type, &fmt, &ch, &bits);
            glen(sub, &len, FMOD_TIMEUNIT_PCM);
            gloop(sub, &ls, FMOD_TIMEUNIT_PCM, &le, FMOD_TIMEUNIT_PCM);
            gdef(sub, &fr, &vol, &pan, &pri);
            if (pass == (dump ? 2 : 1)) {
                printf("  %3d %-30s type %d fmt %d ch %d bits %d len %u loop %u-%u freq %.0f vol %.2f pri %d",
                       i, name, type, fmt, ch, bits, len, ls, le, fr, vol, pri);
                if (pass == 2) {
                    char out[MAX_PATH];
                    FILE *f;
                    static short buf[16384];
                    unsigned got = 0, total = 0, want = len * 2 * (ch ? ch : 1), chunk;
                    sprintf(out, "%s\\%03d.raw", dump, i);
                    f = fopen(out, "wb");
                    sk(sub, 0);
                    do {   /* a stream reads on into the next subsounds: stop at this one's length */
                        got = 0;
                        chunk = want - total < sizeof buf ? want - total : sizeof buf;
                        r = rd(sub, buf, chunk, &got);
                        if (got) fwrite(buf, 1, got, f);
                        total += got;
                    } while (!r && got && total < want);
                    fclose(f);
                    printf(" decoded %u frames (end %d)", total / (2 * (ch ? ch : 1)), r);
                }
                printf("\n");
            }
        }
        rel(snd);
    }
    return errors ? 1 : 0;
}

static int check_fev(const char *media, const char *fev, int props)
{
    tEvNumProp np = P(ev, "_FMOD_Event_GetNumProperties@8");
    tEvPropInfo pinf = P(ev, "_FMOD_Event_GetPropertyInfo@16");
    tEvPropGet pget = P(ev, "_FMOD_Event_GetPropertyByIndex@16");
    tEsCreate ec = P(ev, "_FMOD_EventSystem_Create@4");
    tEsSys es_sys = P(ev, "_FMOD_EventSystem_GetSystemObject@8");
    tEsInit ei = P(ev, "_FMOD_EventSystem_Init@20");
    tEsMedia em = P(ev, "_FMOD_EventSystem_SetMediaPath@8");
    tEsLoad el = P(ev, "_FMOD_EventSystem_Load@16");
    tEsUpdate eu = P(ev, "_FMOD_EventSystem_Update@4");
    tEsVersion evv = P(ev, "_FMOD_EventSystem_GetVersion@8");
    tPrjNum pn = P(ev, "_FMOD_EventProject_GetNumEvents@8");
    tPrjGet pg = P(ev, "_FMOD_EventProject_GetEventByProjectID@16");
    tEvInfo gi = P(ev, "_FMOD_Event_GetInfo@16");
    tEvStart st = P(ev, "_FMOD_Event_Start@4");
    tEvStop sp = P(ev, "_FMOD_Event_Stop@8");
    tSysSetOutput so = P(ex, "_FMOD_System_SetOutput@8");
    void *es = 0, *sys = 0, *prj = 0;
    unsigned ver = 0;
    int r, n = 0, i, ok = 0, bad = 0, k;
    if ((r = ec(&es)) || (r = es_sys(es, &sys)) || (r = so(sys, OUT_NOSOUND)) || (r = ei(es, 64, 0, 0, 0))) {
        printf("event system init error %d\n", r);
        return 2;
    }
    evv(es, &ver);
    printf("FMOD event system %08X\n", ver);
    if ((r = em(es, media))) { printf("setMediaPath error %d\n", r); return 2; }
    r = el(es, fev, 0, &prj);
    if (r) { printf("load %s error %d\n", fev, r); return 1; }
    pn(prj, &n);
    printf("loaded %s: %d events\n", fev, n);
    for (i = 0; i < n; i++) {
        void *e = 0;
        char *name = 0;
        int idx = 0;
        r = pg(prj, i, props ? 4 /* FMOD_EVENT_INFOONLY */ : 0, &e);
        if (r || !e) { printf("  event %d: getEvent error %d\n", i, r); bad++; continue; }
        gi(e, &idx, &name, 0);
        if (props) {   /* dump every event property (diff the output of two .fev files) */
            int m = 0, j;
            np(e, &m);
            for (j = 0; j < m; j++) {
                int pi = j, type = -1;
                char *pn = 0;
                union { int i; float f; char *s; unsigned char b[16]; } v;
                memset(&v, 0, sizeof v);
                pinf(e, &pi, &pn, &type);
                if (pget(e, j, &v, 0)) { printf("  %s.%s = ?\n", name, pn ? pn : "?"); continue; }
                if (type == 1) printf("  %s.%s = %g\n", name, pn ? pn : "?", v.f);
                else if (type == 2) printf("  %s.%s = \"%s\"\n", name, pn ? pn : "?", v.s ? v.s : "");
                else printf("  %s.%s = %d\n", name, pn ? pn : "?", v.i);
            }
            ok++;
            continue;
        }
        r = st(e);
        for (k = 0; k < 3; k++) eu(es);
        sp(e, 1);
        if (r) printf("  event %d %s: start error %d\n", i, name ? name : "?", r);
        ok++;
    }
    printf("events ok %d, failed %d\n", ok, bad);
    return bad ? 1 : 0;
}

int main(int argc, char **argv)
{
    char p[MAX_PATH];
    if (argc < 4) {
        printf("usage: fmodcheck <dll dir> fsb <bank.fsb> [dump dir]\n       fmodcheck <dll dir> fev <media dir\\> <name.fev>\n");
        return 2;
    }
    SetDllDirectoryA(argv[1]);
    sprintf(p, "%s\\fmodex.dll", argv[1]);
    ex = LoadLibraryA(p);
    sprintf(p, "%s\\fmod_event.dll", argv[1]);
    ev = LoadLibraryA(p);
    if (!ex || !ev) { printf("cannot load FMOD DLLs from %s\n", argv[1]); return 2; }
    if (!strcmp(argv[2], "fsb")) return check_fsb(argv[3], argc > 4 ? argv[4] : 0);
    if (!strcmp(argv[2], "fev") && argc > 4) return check_fev(argv[3], argv[4], 0);
    if (!strcmp(argv[2], "fevprops") && argc > 4) return check_fev(argv[3], argv[4], 1);
    printf("bad mode\n");
    return 2;
}
