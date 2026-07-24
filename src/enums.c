#include "enums.h"

#include <string.h>

/* The rule registry is a prefix of the coverage registry, in order. If either
 * list is reordered or a rule is inserted anywhere but the end, one of these
 * fails at compile time rather than silently mislabelling every report. */
_Static_assert(QC_RULE_ID_COUNT == 14, "rule registry is append-only: adding a rule is deliberate");
_Static_assert(QC_COVERAGE_CLASS_COUNT == QC_RULE_ID_COUNT + 4,
               "coverage classes are the rule registry plus the four budget classes");
_Static_assert((int)QC_RULE_pulse_length_range == (int)QC_COV_pulse_length_range, "");
_Static_assert((int)QC_RULE_negative_time == (int)QC_COV_negative_time, "");
_Static_assert((int)QC_COV_pmem_words == QC_RULE_ID_COUNT,
               "the budget classes must follow the rule registry, not interleave with it");

/* Name tables. Each is generated from the same registry as its enum, so an
 * entry cannot exist in one and not the other. */

#define X(n) #n,
static const char *const severity_names[] = {QC_SEVERITIES(X)};
static const char *const quantity_names[] = {QC_QUANTITIES(X)};
static const char *const round_mode_names[] = {QC_ROUND_MODES(X)};
static const char *const frames_mode_names[] = {QC_FRAMES_MODES(X)};
static const char *const channel_kind_names[] = {QC_CHANNEL_KINDS(X)};
static const char *const shape_names[] = {QC_SHAPES(X)};
static const char *const rule_id_names[] = {QC_RULE_IDS(X)};
static const char *const repair_id_names[] = {QC_REPAIR_IDS(X)};
static const char *const budget_id_names[] = {QC_BUDGET_IDS(X)};
static const char *const cost_kind_names[] = {QC_COST_KINDS(X)};
static const char *const verdict_names[] = {QC_VERDICTS(X)};
static const char *const budget_verdict_names[] = {QC_BUDGET_VERDICTS(X)};
static const char *const coverage_status_names[] = {QC_COVERAGE_STATUSES(X)};
static const char *const coverage_class_names[] = {QC_COVERAGE_CLASSES(X)};
#undef X

/* An out-of-range value is a bug in the checker, not bad input, so it gets a
 * marker that will be conspicuous in a report rather than a crash. */
static const char *lookup_name(const char *const *table, int count, int v) {
    if (v < 0 || v >= count) return "?";
    return table[v];
}

static bool lookup_value(const char *const *table, int count, const char *s,
                         size_t len, int *out) {
    int i;
    for (i = 0; i < count; i++) {
        if (strlen(table[i]) == len && memcmp(table[i], s, len) == 0) {
            *out = i;
            return true;
        }
    }
    return false;
}

#define QC_NAME_FN(fn, Type, table, COUNT) \
    const char *fn(Type v) { return lookup_name(table, COUNT, (int)v); }

QC_NAME_FN(severity_name, Severity, severity_names, QC_SEVERITY_COUNT)
QC_NAME_FN(quantity_name, Quantity, quantity_names, QC_QUANTITY_COUNT)
QC_NAME_FN(round_mode_name, RoundMode, round_mode_names, QC_ROUND_MODE_COUNT)
QC_NAME_FN(frames_mode_name, FramesMode, frames_mode_names, QC_FRAMES_MODE_COUNT)
QC_NAME_FN(channel_kind_name, ChannelKind, channel_kind_names, QC_CHANNEL_KIND_COUNT)
QC_NAME_FN(shape_name, Shape, shape_names, QC_SHAPE_COUNT)
QC_NAME_FN(rule_id_name, RuleId, rule_id_names, QC_RULE_ID_COUNT)
QC_NAME_FN(repair_id_name, RepairId, repair_id_names, QC_REPAIR_ID_COUNT)
QC_NAME_FN(budget_id_name, BudgetId, budget_id_names, QC_BUDGET_ID_COUNT)
QC_NAME_FN(cost_kind_name, CostKind, cost_kind_names, QC_COST_KIND_COUNT)
QC_NAME_FN(verdict_name, Verdict, verdict_names, QC_VERDICT_COUNT)
QC_NAME_FN(budget_verdict_name, BudgetVerdict, budget_verdict_names, QC_BUDGET_VERDICT_COUNT)
QC_NAME_FN(coverage_status_name, CoverageStatus, coverage_status_names, QC_COVERAGE_STATUS_COUNT)
QC_NAME_FN(coverage_class_name, CoverageClass, coverage_class_names, QC_COVERAGE_CLASS_COUNT)

#define QC_FROM_NAME_FN(fn, Type, table, COUNT)                     \
    bool fn(const char *s, size_t len, Type *out) {                 \
        int v;                                                      \
        if (!lookup_value(table, COUNT, s, len, &v)) return false;   \
        *out = (Type)v;                                             \
        return true;                                                \
    }

QC_FROM_NAME_FN(severity_from_name, Severity, severity_names, QC_SEVERITY_COUNT)
QC_FROM_NAME_FN(quantity_from_name, Quantity, quantity_names, QC_QUANTITY_COUNT)
QC_FROM_NAME_FN(round_mode_from_name, RoundMode, round_mode_names, QC_ROUND_MODE_COUNT)
QC_FROM_NAME_FN(frames_mode_from_name, FramesMode, frames_mode_names, QC_FRAMES_MODE_COUNT)
QC_FROM_NAME_FN(channel_kind_from_name, ChannelKind, channel_kind_names, QC_CHANNEL_KIND_COUNT)
QC_FROM_NAME_FN(shape_from_name, Shape, shape_names, QC_SHAPE_COUNT)
QC_FROM_NAME_FN(rule_id_from_name, RuleId, rule_id_names, QC_RULE_ID_COUNT)
QC_FROM_NAME_FN(budget_id_from_name, BudgetId, budget_id_names, QC_BUDGET_ID_COUNT)
QC_FROM_NAME_FN(cost_kind_from_name, CostKind, cost_kind_names, QC_COST_KIND_COUNT)
