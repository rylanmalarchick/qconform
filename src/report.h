#ifndef QCONFORM_REPORT_H
#define QCONFORM_REPORT_H

#include <stddef.h>

#include "arena.h"
#include "check.h"

/* Render a report as report-format-v0 JSON. Returns arena memory and writes
 * its length, or NULL on allocation failure. The bytes are not NUL-terminated
 * by contract; use the length. */
char *render_report(Arena *a, const Report *r, size_t *out_len);

#endif /* QCONFORM_REPORT_H */
