/* qconform <descriptor.json> <program.json>
 *
 * Report JSON on stdout; exit 0 pass, 1 fail, 2 pass_with_repairs, 3 tool
 * error (usage, io, malformed input, invalid descriptor).
 *
 * Unrealizable is a normal result. Only exit 3 is an abnormal one.
 */

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "arena.h"
#include "check.h"
#include "parse.h"
#include "report.h"

#define MAX_INPUT (64u * 1024u * 1024u)

static int tool_error(const char *fmt, ...) {
    va_list ap;
    fputs("qconform: ", stderr);
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    return 3;
}

static const char *USAGE = "usage: qconform <descriptor.json> <program.json>";

/* Read a whole file into the arena. Returns NULL and sets *why on failure. */
static char *read_file(Arena *a, const char *path, size_t *out_len, const char **why) {
    FILE *f = fopen(path, "rb");
    char *buf;
    size_t cap = 1 << 16, len = 0;

    if (f == NULL) {
        *why = "cannot open";
        return NULL;
    }
    buf = arena_alloc(a, cap, 1);
    if (buf == NULL) {
        fclose(f);
        *why = "out of memory";
        return NULL;
    }
    for (;;) {
        size_t got;
        if (len == cap) {
            char *bigger;
            if (cap >= MAX_INPUT) {
                fclose(f);
                *why = "file too large";
                return NULL;
            }
            cap *= 2;
            bigger = arena_alloc(a, cap, 1);
            if (bigger == NULL) {
                fclose(f);
                *why = "out of memory";
                return NULL;
            }
            memcpy(bigger, buf, len);
            buf = bigger;
        }
        got = fread(buf + len, 1, cap - len, f);
        len += got;
        if (got == 0) break;
    }
    if (ferror(f)) {
        fclose(f);
        *why = "read error";
        return NULL;
    }
    fclose(f);
    *out_len = len;
    return buf;
}

int main(int argc, char **argv) {
    Arena *a;
    Diag diag;
    Descriptor desc;
    IrProgram prog;
    Report report;
    char *desc_bytes, *prog_bytes, *out;
    size_t desc_len, prog_len, out_len;
    const char *why;
    int status;

    if (argc != 3) return tool_error("%s", USAGE);

    a = arena_new();
    if (a == NULL) return tool_error("out of memory");

    desc_bytes = read_file(a, argv[1], &desc_len, &why);
    if (desc_bytes == NULL) {
        arena_destroy(a);
        return tool_error("cannot read '%s': %s", argv[1], why);
    }
    prog_bytes = read_file(a, argv[2], &prog_len, &why);
    if (prog_bytes == NULL) {
        arena_destroy(a);
        return tool_error("cannot read '%s': %s", argv[2], why);
    }

    if (!parse_descriptor(a, desc_bytes, desc_len, &desc, &diag)) {
        arena_destroy(a);
        return tool_error("invalid descriptor: %s", diag_msg(&diag));
    }
    if (!parse_program(a, prog_bytes, prog_len, &prog, &diag)) {
        arena_destroy(a);
        return tool_error("invalid program: %s", diag_msg(&diag));
    }
    if (!check(a, &prog, &desc, &report, &diag)) {
        arena_destroy(a);
        return tool_error("check failed: %s", diag_msg(&diag));
    }

    out = render_report(a, &report, &out_len);
    if (out == NULL) {
        arena_destroy(a);
        return tool_error("out of memory rendering report");
    }
    if (out_len != 0 && fwrite(out, 1, out_len, stdout) != out_len) {
        arena_destroy(a);
        return tool_error("cannot write report");
    }
    if (fflush(stdout) != 0) {
        arena_destroy(a);
        return tool_error("cannot write report");
    }

    switch (report.verdict) {
    case QC_VERDICT_pass: status = 0; break;
    case QC_VERDICT_fail: status = 1; break;
    case QC_VERDICT_pass_with_repairs: status = 2; break;
    default: status = 3; break;
    }
    arena_destroy(a);
    return status;
}
