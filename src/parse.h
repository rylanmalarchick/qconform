#ifndef QCONFORM_PARSE_H
#define QCONFORM_PARSE_H

/* JSON bytes -> IrProgram / Descriptor.
 *
 * Any failure here is a tool error (exit 3), never a rejection: a program the
 * device cannot realize is data, but a program that is not a program is not
 * an answer. Enforced beyond the schema: no floats, i64 bounds, canonical
 * rationals, strictly positive time bases, unique names and element ids, no
 * dangling references, no unknown fields.
 */

#include <stdbool.h>
#include <stddef.h>

#include "arena.h"
#include "capability.h"
#include "ir.h"

/* Diagnostic buffer. Fixed size: a diagnostic longer than
 * this is truncated rather than allocated, because it is on the failure path
 * and the failure path must not itself fail. */
typedef struct {
    char buf[256];
} Diag;

void diag_set(Diag *d, const char *fmt, ...);
const char *diag_msg(const Diag *d);

/* Both return true on success. On failure they write *diag and leave the
 * output untouched. */
bool parse_program(Arena *a, const char *bytes, size_t len, IrProgram *out, Diag *diag);
bool parse_descriptor(Arena *a, const char *bytes, size_t len, Descriptor *out, Diag *diag);

#endif /* QCONFORM_PARSE_H */
