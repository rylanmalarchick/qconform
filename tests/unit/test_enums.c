#include "../../src/enums.c"
#include "../../src/capability.h"
#include "../../src/ir.h"
#include "test.h"

TEST(names_round_trip) {
    int i;
    for (i = 0; i < QC_RULE_ID_COUNT; i++) {
        RuleId back = (RuleId)-1;
        const char *n = rule_id_name((RuleId)i);
        CHECK(rule_id_from_name(n, strlen(n), &back));
        CHECK_I64(back, i);
    }
    for (i = 0; i < QC_COVERAGE_CLASS_COUNT; i++) CHECK(coverage_class_name((CoverageClass)i)[0] != '?');
    for (i = 0; i < QC_REPAIR_ID_COUNT; i++) CHECK(repair_id_name((RepairId)i)[0] != '?');
}

TEST(rule_class_is_identity_on_prefix) {
    int i;
    for (i = 0; i < QC_RULE_ID_COUNT; i++)
        CHECK_STR(coverage_class_name(rule_class((RuleId)i)), rule_id_name((RuleId)i));
}

TEST(known_names_and_ordinals) {
    CHECK_STR(rule_id_name(QC_RULE_pulse_length_range), "pulse_length_range");
    CHECK_STR(coverage_class_name(QC_COV_unconstrained_channel), "unconstrained_channel");
    CHECK_STR(verdict_name(QC_VERDICT_pass_with_repairs), "pass_with_repairs");
    CHECK_STR(severity_name(QC_SEV_vendor_repairable), "vendor_repairable");
    CHECK_STR(budget_id_name(QC_BUDGET_wmem_words), "wmem_words");
    /* ordinal order is load-bearing for the coverage list and the sort */
    CHECK_I64(QC_RULE_pulse_length_range, 0);
    CHECK_I64(QC_RULE_negative_time, 13);
    CHECK_I64(QC_COV_pmem_words, 14);
}

TEST(lookup_rejects_unknown_and_respects_length) {
    RuleId r;
    CHECK(!rule_id_from_name("made_up_rule", 12, &r));
    CHECK(!rule_id_from_name("", 0, &r));
    /* a prefix must not match a longer registry entry, nor vice versa */
    CHECK(!rule_id_from_name("pulse_length_rang", 17, &r));
    CHECK(!rule_id_from_name("pulse_length_ranges", 19, &r));
    /* an embedded NUL cannot truncate the comparison */
    CHECK(!rule_id_from_name("pulse_length_range\0x", 20, &r));
    CHECK(rule_id_from_name("pulse_length_range", 18, &r));
}

TEST(out_of_range_is_marked_not_crashed) {
    CHECK_STR(rule_id_name((RuleId)999), "?");
    CHECK_STR(verdict_name((Verdict)-1), "?");
}

TEST(structs_are_usable) {
    IrElement e; IrWaveform w; Constraint c;
    e.id = 7; e.kind = EL_PLAY; e.as.play.duration = 280;
    CHECK_I64(e.id, 7);
    w.name = str_lit("w0"); w.kind = WF_CONST; w.as.constant.amplitude = rat_from_int(1);
    CHECK(str_eq_lit(w.name, "w0"));
    c.has_min_units = false; c.min_units = 0;
    CHECK(!c.has_min_units);
}

TEST_MAIN_BEGIN
RUN(names_round_trip);
RUN(rule_class_is_identity_on_prefix);
RUN(known_names_and_ordinals);
RUN(lookup_rejects_unknown_and_respects_length);
RUN(out_of_range_is_marked_not_crashed);
RUN(structs_are_usable);
TEST_MAIN_END
