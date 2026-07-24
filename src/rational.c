#include "rational.h"

/* Reduce a signed 128-bit fraction back into canonical 64-bit form. Returns
 * false if it does not fit after reduction. den must be nonzero. */
static bool reduce(i128 num, i128 den, Rat *out) {
    u128 un, ud, g, rn, rd;
    bool neg;

    if (num == 0) {
        *out = RAT_ZERO;
        return true;
    }
    neg = (num < 0) != (den < 0);
    un = abs_i128(num);
    ud = abs_i128(den);
    g = gcd_u128(un, ud);
    rn = un / g;
    rd = ud / g;
    if (rn > (u128)INT64_MAX || rd > (u128)INT64_MAX) return false;
    out->num = neg ? -(int64_t)rn : (int64_t)rn;
    out->den = (int64_t)rd;
    return true;
}

bool rat_is_canonical(Rat r) {
    if (r.den <= 0) return false;
    if (r.num == 0) return r.den == 1;
    return gcd_u128((u128)abs_i64(r.num), (u128)abs_i64(r.den)) == 1;
}

bool rat_eq(Rat a, Rat b) { return a.num == b.num && a.den == b.den; }

int rat_cmp(Rat a, Rat b) {
    i128 l = (i128)a.num * b.den;
    i128 r = (i128)b.num * a.den;
    if (l < r) return -1;
    if (l > r) return 1;
    return 0;
}

bool rat_mul(Rat a, Rat b, Rat *out) {
    return reduce((i128)a.num * b.num, (i128)a.den * b.den, out);
}

bool rat_div(Rat a, Rat b, Rat *out) {
    if (b.num == 0) return false;
    return reduce((i128)a.num * b.den, (i128)a.den * b.num, out);
}

bool rat_mul_int(Rat a, int64_t k, Rat *out) {
    return reduce((i128)a.num * k, (i128)a.den, out);
}

bool rat_add(Rat a, Rat b, Rat *out) {
    i128 num = (i128)a.num * b.den + (i128)b.num * a.den;
    if (num == 0) {
        *out = RAT_ZERO;
        return true;
    }
    return reduce(num, (i128)a.den * b.den, out);
}

bool rat_exact_div(Rat a, Rat b, int64_t *out, bool *exact) {
    Rat q;
    if (!rat_div(a, b, &q)) return false;
    *exact = (q.den == 1);
    if (*exact) *out = q.num;
    return true;
}

bool rat_exact_mul_int(Rat a, int64_t k, int64_t *out, bool *exact) {
    Rat p;
    if (!rat_mul_int(a, k, &p)) return false;
    *exact = (p.den == 1);
    if (*exact) *out = p.num;
    return true;
}

int64_t rat_round_nearest_even(Rat r) {
    /* floor_div/floor_mod, not / and %: for a negative numerator the
     * truncating forms pick a different branch below. */
    int64_t q = floor_div(r.num, r.den);
    int64_t rem = floor_mod(r.num, r.den);
    i128 twice = 2 * (i128)rem;
    if (twice < r.den) return q;
    if (twice > r.den) return q + 1;
    return floor_mod(q, 2) == 0 ? q : q + 1;
}
