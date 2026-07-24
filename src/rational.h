#ifndef QCONFORM_RATIONAL_H
#define QCONFORM_RATIONAL_H

/* Exact rational arithmetic for the checker core.
 *
 * Canonical form: den > 0, gcd(|num|, den) == 1. Parsing enforces it on
 * input; every operation here restores it. Overflow is always reported, never
 * wrapped: a checker that silently wraps produces a wrong verdict, which is
 * worse than refusing to answer.
 */

#include <stdbool.h>
#include <stdint.h>

#include "intmath.h"

typedef struct {
    int64_t num;
    int64_t den;
} Rat;

#define RAT_ZERO ((Rat){0, 1})

static inline Rat rat_from_int(int64_t n) {
    Rat r = {n, 1};
    return r;
}

bool rat_is_canonical(Rat r);
bool rat_eq(Rat a, Rat b);

/* -1, 0, 1 for a < b, a == b, a > b. Exact: cross-multiplies in 128 bits. */
int rat_cmp(Rat a, Rat b);

/* Each returns true on success, false on overflow (or division by zero for
 * the div forms), leaving *out untouched on failure. */
bool rat_mul(Rat a, Rat b, Rat *out);
bool rat_div(Rat a, Rat b, Rat *out);
bool rat_mul_int(Rat a, int64_t k, Rat *out);
bool rat_add(Rat a, Rat b, Rat *out);

/* a / b when the quotient is an exact integer. On success sets *exact to
 * whether it was, and *out to the quotient when it was. */
bool rat_exact_div(Rat a, Rat b, int64_t *out, bool *exact);

/* k * a when the product is an exact integer; same convention. */
bool rat_exact_mul_int(Rat a, int64_t k, int64_t *out, bool *exact);

/* Round to nearest integer, ties to even (the vendor rounding mode). */
int64_t rat_round_nearest_even(Rat r);

#endif /* QCONFORM_RATIONAL_H */
