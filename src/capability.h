#ifndef QCONFORM_CAPABILITY_H
#define QCONFORM_CAPABILITY_H

/* The device capability descriptor, pure data. Evidence and vendor_behavior
 * entries are authoring-side: parse.c type-checks their containers and drops
 * the contents.
 *
 * Optional fields carry an explicit has_ flag rather than a sentinel. This is
 * a checker whose entire job is bounds, and a sentinel is how -1 quietly
 * starts meaning "absent" for a field where -1 is a legal value.
 */

#include <stdbool.h>
#include <stdint.h>

#include "enums.h"
#include "str.h"
#include "rational.h"

typedef struct {
    Str name;
    Str board;
    Str fw_timestamp;
    Str cfg_sw_version;
    Str library_name;
    Str library_version;
    Str descriptor_version;
} Identification;

typedef struct {
    RoundMode time;
    RoundMode frequency;
    RoundMode phase;
    RoundMode amplitude;
} Rounding;

typedef struct {
    RuleId id;
    Quantity quantity;
    Shape shape;
    Severity severity;

    int64_t min_units;
    bool has_min_units;
    int64_t max_units;
    bool has_max_units;

    Rat min;
    bool has_min;
    Rat max;
    bool has_max;
    Rat resolution;
    bool has_resolution;

    bool post_mixer;

    int64_t grid;
    bool has_grid;
} Constraint;

typedef struct {
    bool phrst;
    bool has_phrst;
    int64_t n_tones;
    bool has_n_tones;
    int64_t envelope_memory_samples;
    bool has_envelope_memory_samples;
    int64_t envelope_sample_grid;
    bool has_envelope_sample_grid;
    int64_t envelope_max_abs;
    bool has_envelope_max_abs;
} Capabilities;

typedef struct {
    Str name;
    ChannelKind kind;
    Str vendor_type;
    Rat unit;
    Rat sample_unit;
    bool has_sample_unit;
    int64_t duration_grid;
    int64_t schedule_grid;
    Capabilities capabilities;
    const Constraint *constraints;
    size_t n_constraints;
} CapChannel;

typedef struct {
    CostKind kind;
    int64_t per_item;  /* defaults to 1 */
    int64_t overhead;  /* defaults to 0 */
    int64_t reserved_min;
    bool has_reserved_min;
    int64_t reserved_max;
    bool has_reserved_max;
} CostModel;

typedef struct {
    BudgetId id;
    int64_t limit;
    CostModel cost;
} Budget;

typedef struct {
    Identification ident;
    FramesMode frames;
    Rounding rounding;
    const CapChannel *channels;
    size_t n_channels;
    const Budget *budgets;
    size_t n_budgets;
} Descriptor;

#endif /* QCONFORM_CAPABILITY_H */
