#ifndef QCONFORM_ENUMS_H
#define QCONFORM_ENUMS_H

/* Every enumeration the formats serialize, as X-macro registries.
 *
 * Each registry is written once and generates three things: the C enum, the
 * name table used for output, and the name lookup used for input. They cannot
 * drift from one another, which is what the Zig got for free from @tagName
 * and std.meta.stringToEnum.
 *
 * Ordinals are internal. Ids serialize as these names, never as numbers, and
 * the registries are append-only: a rule id is never renamed, renumbered, or
 * reused, and a retired id keeps its slot reserved (report-format-v0.txt).
 * Ordinal ORDER is still load-bearing in two places — the report lists
 * coverage classes in registry order, and rejections sort by rule ordinal —
 * so appending is safe but reordering is not.
 */

#include <stdbool.h>
#include <stddef.h>

/* --- registries ----------------------------------------------------------- */

#define QC_SEVERITIES(X) \
    X(fatal)             \
    X(vendor_repairable)

#define QC_QUANTITIES(X) \
    X(time)              \
    X(frequency)         \
    X(phase)             \
    X(amplitude)         \
    X(count)

#define QC_ROUND_MODES(X) \
    X(nearest_half_even)  \
    X(trunc_toward_zero)

#define QC_FRAMES_MODES(X) \
    X(native)              \
    X(compiled_away)

#define QC_CHANNEL_KINDS(X) \
    X(drive)                \
    X(readout)

#define QC_SHAPES(X)     \
    X(range_units)       \
    X(range_resolution)  \
    X(grid_samples)

/* The append-only rule registry. A descriptor constraint id outside this set
 * is an invalid descriptor. Structural ids (schedule_grid, negative_*) are
 * checker-owned rather than descriptor-declared. */
#define QC_RULE_IDS(X)      \
    X(pulse_length_range)   \
    X(pulse_length_grid)    \
    X(readout_length_range) \
    X(frequency_range)      \
    X(frequency_resolution) \
    X(phase_resolution)     \
    X(amplitude_range)      \
    X(amplitude_resolution) \
    X(envelope_sample_grid) \
    X(envelope_amplitude)   \
    X(envelope_memory)      \
    X(schedule_grid)        \
    X(negative_duration)    \
    X(negative_time)

#define QC_REPAIR_IDS(X)  \
    X(quantize_duration)  \
    X(quantize_time)      \
    X(quantize_frequency) \
    X(quantize_phase)     \
    X(trunc_gain)         \
    X(alias_mod_f_dds)

#define QC_BUDGET_IDS(X) \
    X(pmem_words)        \
    X(wmem_words)        \
    X(loop_registers)

#define QC_COST_KINDS(X) \
    X(linear)            \
    X(indeterminate_band)

#define QC_VERDICTS(X)   \
    X(pass)              \
    X(pass_with_repairs) \
    X(fail)

#define QC_BUDGET_VERDICTS(X) \
    X(fits)                   \
    X(violates)               \
    X(indeterminate)

#define QC_COVERAGE_STATUSES(X) \
    X(checked)                  \
    X(not_applicable)           \
    X(unchecked)                \
    X(indeterminate)

/* Coverage classes are the rule registry plus the budget classes. Defining
 * it as a literal superset is what makes rule_class() the identity on the
 * first QC_RULE_ID_COUNT ordinals; the static assertions in enums.c pin that
 * relationship so the two lists cannot drift apart. */
#define QC_COVERAGE_CLASSES(X) \
    QC_RULE_IDS(X)             \
    X(pmem_words)              \
    X(wmem_words)              \
    X(loop_registers)          \
    X(unconstrained_channel)

/* --- generated types ------------------------------------------------------ */

#define QC_ENUM_ENTRY(n) QC_ENUM_PREFIX##n,

#define X(n) QC_SEV_##n,
typedef enum { QC_SEVERITIES(X) QC_SEVERITY_COUNT } Severity;
#undef X

#define X(n) QC_QTY_##n,
typedef enum { QC_QUANTITIES(X) QC_QUANTITY_COUNT } Quantity;
#undef X

#define X(n) QC_ROUND_##n,
typedef enum { QC_ROUND_MODES(X) QC_ROUND_MODE_COUNT } RoundMode;
#undef X

#define X(n) QC_FRAMES_##n,
typedef enum { QC_FRAMES_MODES(X) QC_FRAMES_MODE_COUNT } FramesMode;
#undef X

#define X(n) QC_CHKIND_##n,
typedef enum { QC_CHANNEL_KINDS(X) QC_CHANNEL_KIND_COUNT } ChannelKind;
#undef X

#define X(n) QC_SHAPE_##n,
typedef enum { QC_SHAPES(X) QC_SHAPE_COUNT } Shape;
#undef X

#define X(n) QC_RULE_##n,
typedef enum { QC_RULE_IDS(X) QC_RULE_ID_COUNT } RuleId;
#undef X

#define X(n) QC_REPAIR_##n,
typedef enum { QC_REPAIR_IDS(X) QC_REPAIR_ID_COUNT } RepairId;
#undef X

#define X(n) QC_BUDGET_##n,
typedef enum { QC_BUDGET_IDS(X) QC_BUDGET_ID_COUNT } BudgetId;
#undef X

#define X(n) QC_COST_##n,
typedef enum { QC_COST_KINDS(X) QC_COST_KIND_COUNT } CostKind;
#undef X

#define X(n) QC_VERDICT_##n,
typedef enum { QC_VERDICTS(X) QC_VERDICT_COUNT } Verdict;
#undef X

#define X(n) QC_BVERDICT_##n,
typedef enum { QC_BUDGET_VERDICTS(X) QC_BUDGET_VERDICT_COUNT } BudgetVerdict;
#undef X

#define X(n) QC_CSTAT_##n,
typedef enum { QC_COVERAGE_STATUSES(X) QC_COVERAGE_STATUS_COUNT } CoverageStatus;
#undef X

#define X(n) QC_COV_##n,
typedef enum { QC_COVERAGE_CLASSES(X) QC_COVERAGE_CLASS_COUNT } CoverageClass;
#undef X

/* --- names and lookup ------------------------------------------------------ */

const char *severity_name(Severity v);
const char *quantity_name(Quantity v);
const char *round_mode_name(RoundMode v);
const char *frames_mode_name(FramesMode v);
const char *channel_kind_name(ChannelKind v);
const char *shape_name(Shape v);
const char *rule_id_name(RuleId v);
const char *repair_id_name(RepairId v);
const char *budget_id_name(BudgetId v);
const char *cost_kind_name(CostKind v);
const char *verdict_name(Verdict v);
const char *budget_verdict_name(BudgetVerdict v);
const char *coverage_status_name(CoverageStatus v);
const char *coverage_class_name(CoverageClass v);

/* Each returns true and writes *out on a match; false leaves *out alone.
 * `len` is the name's length, so keys carrying an embedded NUL cannot match
 * a shorter registry entry by accident. */
bool severity_from_name(const char *s, size_t len, Severity *out);
bool quantity_from_name(const char *s, size_t len, Quantity *out);
bool round_mode_from_name(const char *s, size_t len, RoundMode *out);
bool frames_mode_from_name(const char *s, size_t len, FramesMode *out);
bool channel_kind_from_name(const char *s, size_t len, ChannelKind *out);
bool shape_from_name(const char *s, size_t len, Shape *out);
bool rule_id_from_name(const char *s, size_t len, RuleId *out);
bool budget_id_from_name(const char *s, size_t len, BudgetId *out);
bool cost_kind_from_name(const char *s, size_t len, CostKind *out);

/* A rule id names a coverage class of the same name. Free by construction:
 * the coverage registry is the rule registry plus four, in order. */
static inline CoverageClass rule_class(RuleId id) { return (CoverageClass)id; }

#endif /* QCONFORM_ENUMS_H */
