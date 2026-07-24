#ifndef QCONFORM_JSON_H
#define QCONFORM_JSON_H

/* A JSON reader for exactly the grammar qconform accepts.
 *
 * This is deliberately not a general JSON parser. The formats it reads
 * (program-format-v0, descriptor-format-v0) forbid floats outright and
 * require every integer to survive a 64-bit round trip, so:
 *
 *   - a number token containing '.', 'e', or 'E' is rejected at the lexer.
 *     No strtod, no locale, no float ever exists in the process.
 *   - an integer outside i64 is rejected at the lexer, not silently widened.
 *
 * Objects are stored as an ordered array of members with linear lookup.
 * Members number at most a dozen anywhere in these formats, so a hash map
 * would be slower, and an ordered array removes iteration-order
 * nondeterminism by construction — the report must be byte-identical for
 * identical input.
 *
 * Everything is allocated from the caller's arena and is valid until the
 * arena is destroyed. Parsing borrows nothing from the input buffer:
 * strings are unescaped into arena memory.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "arena.h"

typedef enum {
    JSON_NULL,
    JSON_BOOL,
    JSON_INT,
    /* A syntactically valid JSON number that this reader will not turn into a
     * value: it has a fractional or exponent part, or it does not fit i64.
     * The token is recognized and classified but never converted, so no
     * floating-point conversion exists in the process. Carrying the two cases
     * as distinct kinds lets the typed accessors in parse.c report them with
     * the offending field's path, exactly as the Zig implementation does. */
    JSON_FLOAT,
    JSON_BIGINT,
    JSON_STRING,
    JSON_ARRAY,
    JSON_OBJECT
} JsonKind;

typedef struct JsonValue JsonValue;

typedef struct {
    const char *key; /* NUL-terminated, unescaped */
    size_t key_len;  /* length excluding the NUL; keys may contain NUL */
    JsonValue *value;
} JsonMember;

struct JsonValue {
    JsonKind kind;
    union {
        bool boolean;
        int64_t integer;
        struct {
            const char *ptr; /* NUL-terminated, unescaped */
            size_t len;      /* length excluding the NUL */
        } string;
        struct {
            JsonValue **items;
            size_t len;
        } array;
        struct {
            JsonMember *members;
            size_t len;
        } object;
    } as;
};

/* Parse `len` bytes. On success returns the root value; on failure returns
 * NULL and writes a message (never truncated past `err_size - 1`) into `err`.
 *
 * Rejects, among the usual syntax errors: floats, integers outside i64,
 * trailing content, unterminated strings, lone surrogates, raw control
 * characters inside strings, and nesting deeper than JSON_MAX_DEPTH. */
#define JSON_MAX_DEPTH 64

JsonValue *json_parse(Arena *a, const char *bytes, size_t len, char *err, size_t err_size);

/* Member lookup by NUL-terminated name; NULL if absent. Linear. */
JsonValue *json_get(const JsonValue *object, const char *name);

#endif /* QCONFORM_JSON_H */
