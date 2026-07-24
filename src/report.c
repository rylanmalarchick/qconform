#include "report.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

/* Report -> report-format-v0 JSON bytes, hand-rolled for a fixed field order:
 * identical input yields byte-identical output. Nothing here consults a hash
 * map, a locale, or a float. */

typedef struct {
    char *buf;
    size_t len;
    size_t cap;
    Arena *arena;
    bool failed;
} W;

static bool w_reserve(W *w, size_t extra) {
    size_t need;
    if (w->failed) return false;
    if (extra <= w->cap - w->len) return true;
    need = w->cap == 0 ? 4096 : w->cap;
    while (need - w->len < extra) {
        if (need > (size_t)-1 / 2) {
            w->failed = true;
            return false;
        }
        need *= 2;
    }
    {
        char *buf = arena_alloc(w->arena, need, 1);
        if (buf == NULL) {
            w->failed = true;
            return false;
        }
        if (w->len != 0) memcpy(buf, w->buf, w->len);
        w->buf = buf;
        w->cap = need;
    }
    return true;
}

static void puts_n(W *w, const char *s, size_t n) {
    if (!w_reserve(w, n)) return;
    memcpy(w->buf + w->len, s, n);
    w->len += n;
}

static void put(W *w, const char *s) { puts_n(w, s, strlen(s)); }

static void putf(W *w, const char *fmt, ...) {
    va_list ap;
    int n;
    if (!w_reserve(w, 512)) return;
    va_start(ap, fmt);
    n = vsnprintf(w->buf + w->len, w->cap - w->len, fmt, ap);
    va_end(ap);
    if (n < 0 || (size_t)n >= w->cap - w->len) {
        w->failed = true;
        return;
    }
    w->len += (size_t)n;
}

static void put_i64(W *w, int64_t v) { putf(w, "%lld", (long long)v); }

static void put_rat(W *w, Rat r) {
    put(w, "{\"num\": ");
    put_i64(w, r.num);
    put(w, ", \"den\": ");
    put_i64(w, r.den);
    put(w, "}");
}

/* Names and versions are vendor strings that arrive from the descriptor. A
 * raw control character inside a JSON string literal is invalid JSON, so the
 * full required set is escaped (RFC 8259: ", \, and U+0000..U+001F). Bytes
 * >= 0x20 pass through, so valid UTF-8 stays valid UTF-8. */
static void put_str_n(W *w, const char *s, size_t n) {
    size_t i;
    put(w, "\"");
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
        case '"': put(w, "\\\""); break;
        case '\\': put(w, "\\\\"); break;
        case 0x08: put(w, "\\b"); break;
        case 0x09: put(w, "\\t"); break;
        case 0x0a: put(w, "\\n"); break;
        case 0x0c: put(w, "\\f"); break;
        case 0x0d: put(w, "\\r"); break;
        default:
            if (c < 0x20)
                putf(w, "\\u%04x", (unsigned)c);
            else
                puts_n(w, (const char *)&c, 1);
            break;
        }
    }
    put(w, "\"");
}

static void put_str(W *w, Str s) { put_str_n(w, s.ptr, s.len); }

char *render_report(Arena *a, const Report *r, size_t *out_len) {
    W w;
    size_t i;

    memset(&w, 0, sizeof w);
    w.arena = a;

    put(&w, "{\n \"format\": \"qconform-report\",\n \"format_version\": 0,\n \"verdict\": ");
    put_str_n(&w, verdict_name(r->verdict), strlen(verdict_name(r->verdict)));

    put(&w, ",\n \"descriptor\": {\"name\": ");
    put_str(&w, r->ident.name);
    put(&w, ", \"firmware\": ");
    put_str(&w, r->ident.fw_timestamp);
    put(&w, ", \"software\": ");
    /* composed from two vendor strings, so the composition is escaped, not
     * the parts: writing them raw is how a quote or backslash in either one
     * breaks the report */
    {
        size_t n = r->ident.library_name.len + 1 + r->ident.library_version.len;
        char *joined = arena_alloc(a, n + 1, 1);
        if (joined == NULL) return NULL;
        memcpy(joined, r->ident.library_name.ptr, r->ident.library_name.len);
        joined[r->ident.library_name.len] = '-';
        memcpy(joined + r->ident.library_name.len + 1, r->ident.library_version.ptr,
               r->ident.library_version.len);
        put_str_n(&w, joined, n);
    }
    put(&w, ", \"descriptor_version\": ");
    put_str(&w, r->ident.descriptor_version);
    put(&w, "},\n \"rejections\": [");

    for (i = 0; i < r->n_rejections; i++) {
        const Rejection *rej = &r->rejections[i];
        put(&w, i == 0 ? "\n" : ",\n");
        putf(&w, "  {\"rule\": \"%s\", \"severity\": \"%s\", ", coverage_class_name(rej->rule),
             severity_name(rej->severity));
        if (rej->has_repair) putf(&w, "\"repair\": \"%s\", ", repair_id_name(rej->repair));
        put(&w, "\"element\": ");
        put_i64(&w, rej->element);
        putf(&w, ", \"quantity\": \"%s\", \"value\": ", quantity_name(rej->quantity));
        put_rat(&w, rej->value);
        put(&w, ", \"limit\": ");
        put_rat(&w, rej->limit);
        put(&w, "}");
    }
    put(&w, r->n_rejections == 0 ? "],\n" : "\n ],\n");

    put(&w, " \"budgets\": [");
    for (i = 0; i < r->n_budgets; i++) {
        const BudgetResult *b = &r->budgets[i];
        put(&w, i == 0 ? "\n" : ",\n");
        putf(&w, "  {\"budget\": \"%s\", \"verdict\": \"%s\", \"lower_bound\": %lld, "
                 "\"upper_bound\": %lld, \"limit\": %lld}",
             budget_id_name(b->id), budget_verdict_name(b->verdict), (long long)b->lower,
             (long long)b->upper, (long long)b->limit);
    }
    put(&w, r->n_budgets == 0 ? "],\n" : "\n ],\n");

    put(&w, " \"coverage\": [\n");
    for (i = 0; i < QC_COVERAGE_CLASS_COUNT; i++) {
        putf(&w, "  {\"class\": \"%s\", \"status\": \"%s\"}%s\n",
             coverage_class_name((CoverageClass)i), coverage_status_name(r->coverage[i]),
             i + 1 == QC_COVERAGE_CLASS_COUNT ? "" : ",");
    }
    put(&w, " ]\n}\n");

    if (w.failed) return NULL;
    *out_len = w.len;
    return w.buf;
}
