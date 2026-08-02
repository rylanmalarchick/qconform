#include "parse.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "json.h"

/* Diagnostic strings are part of the contract. The corpus records stderr for
 * every exit-3 case. Both the wording and the order of the checks decide which
 * message a given input produces, so treat both as load-bearing.
 *
 * This file was ported from a Zig implementation, statement for statement.
 * That implementation is archived at
 * desktop:/mnt/four/archive/qconform-zig-2026-07-24/. */

void diag_set(Diag *d, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(d->buf, sizeof d->buf, fmt, ap);
    va_end(ap);
}

const char *diag_msg(const Diag *d) { return d->buf; }

typedef struct {
    Arena *arena;
    Diag *diag;
} Ctx;

static bool fail(Ctx *c, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(c->diag->buf, sizeof c->diag->buf, fmt, ap);
    va_end(ap);
    return false;
}

/* --- typed accessors ------------------------------------------------------ */

static bool get_object(Ctx *c, const JsonValue *v, const char *path, const JsonValue **out) {
    if (v == NULL || v->kind != JSON_OBJECT) return fail(c, "%s: expected object", path);
    *out = v;
    return true;
}

static bool get_array(Ctx *c, const JsonValue *v, const char *path, const JsonValue **out) {
    if (v == NULL || v->kind != JSON_ARRAY) return fail(c, "%s: expected array", path);
    *out = v;
    return true;
}

static bool get_int(Ctx *c, const JsonValue *v, const char *path, int64_t *out) {
    if (v == NULL) return fail(c, "%s: expected integer", path);
    switch (v->kind) {
    case JSON_INT: *out = v->as.integer; return true;
    case JSON_FLOAT: return fail(c, "%s: float (no floats allowed)", path);
    case JSON_BIGINT: return fail(c, "%s: number outside i64", path);
    default: return fail(c, "%s: expected integer", path);
    }
}

static bool get_string(Ctx *c, const JsonValue *v, const char *path, Str *out) {
    if (v == NULL || v->kind != JSON_STRING) return fail(c, "%s: expected string", path);
    out->ptr = v->as.string.ptr;
    out->len = v->as.string.len;
    return true;
}

static bool get_name(Ctx *c, const JsonValue *v, const char *path, Str *out) {
    if (!get_string(c, v, path, out)) return false;
    if (out->len == 0) return fail(c, "%s: empty name", path);
    return true;
}

static bool get_bool(Ctx *c, const JsonValue *v, const char *path, bool *out) {
    if (v == NULL || v->kind != JSON_BOOL) return fail(c, "%s: expected bool", path);
    *out = v->as.boolean;
    return true;
}

static bool known(Ctx *c, const JsonValue *obj, const char *const *allowed, const char *path) {
    size_t i;
    for (i = 0; i < obj->as.object.len; i++) {
        const JsonMember *m = &obj->as.object.members[i];
        const char *const *a;
        bool ok = false;
        for (a = allowed; *a != NULL; a++) {
            if (strlen(*a) == m->key_len && memcmp(*a, m->key, m->key_len) == 0) {
                ok = true;
                break;
            }
        }
        if (!ok) return fail(c, "%s: unknown field '%.*s'", path, (int)m->key_len, m->key);
    }
    return true;
}

static bool require_field(Ctx *c, const JsonValue *obj, const char *field, const char *path,
                          const JsonValue **out) {
    JsonValue *v = json_get(obj, field);
    if (v == NULL) return fail(c, "%s: missing field '%s'", path, field);
    *out = v;
    return true;
}

/* Same, where the path is a parsed name rather than a literal. */
static bool require_field_s(Ctx *c, const JsonValue *obj, const char *field, Str path,
                            const JsonValue **out) {
    JsonValue *v = json_get(obj, field);
    if (v == NULL) return fail(c, STR_FMT ": missing field '%s'", STR_ARG(path), field);
    *out = v;
    return true;
}

static bool get_rat(Ctx *c, const JsonValue *v, const char *path, Rat *out) {
    const JsonValue *obj = NULL;
    static const char *const allowed[] = {"num", "den", NULL};
    JsonValue *nv, *dv;
    Rat r;

    if (!get_object(c, v, path, &obj)) return false;
    if (!known(c, obj, allowed, path)) return false;
    nv = json_get(obj, "num");
    if (nv == NULL) return fail(c, "%s: missing num", path);
    if (!get_int(c, nv, path, &r.num)) return false;
    dv = json_get(obj, "den");
    if (dv == NULL) return fail(c, "%s: missing den", path);
    if (!get_int(c, dv, path, &r.den)) return false;
    if (!rat_is_canonical(r)) return fail(c, "%s: rational not canonical", path);
    *out = r;
    return true;
}

/* A time base is seconds per unit: it must be strictly positive. get_rat
 * alone cannot enforce this — frequency, phase and amplitude are signed — so
 * unit-valued fields are checked at their own use sites. */
static bool get_positive_rat(Ctx *c, const JsonValue *v, const char *path, Rat *out) {
    Rat r;
    if (!get_rat(c, v, path, &r)) return false;
    if (r.num <= 0) return fail(c, "%s: must be positive", path);
    *out = r;
    return true;
}

#define DEFINE_GET_ENUM(fn, Type, from_name)                                       \
    static bool fn(Ctx *c, const JsonValue *v, const char *path, Type *out) {      \
        Str s = {NULL, 0};                                                          \
        if (!get_string(c, v, path, &s)) return false;                             \
        if (!from_name(s.ptr, s.len, out))                                         \
            return fail(c, "%s: unknown value '" STR_FMT "'", path, STR_ARG(s));   \
        return true;                                                               \
    }

DEFINE_GET_ENUM(get_severity, Severity, severity_from_name)
DEFINE_GET_ENUM(get_quantity, Quantity, quantity_from_name)
DEFINE_GET_ENUM(get_shape, Shape, shape_from_name)
DEFINE_GET_ENUM(get_rule_id, RuleId, rule_id_from_name)
DEFINE_GET_ENUM(get_round_mode, RoundMode, round_mode_from_name)
DEFINE_GET_ENUM(get_frames_mode, FramesMode, frames_mode_from_name)
DEFINE_GET_ENUM(get_channel_kind, ChannelKind, channel_kind_from_name)
DEFINE_GET_ENUM(get_budget_id, BudgetId, budget_id_from_name)
DEFINE_GET_ENUM(get_cost_kind, CostKind, cost_kind_from_name)

static bool check_header(Ctx *c, const JsonValue *obj, const char *format) {
    const JsonValue *v = NULL;
    Str f = {NULL, 0};
    int64_t version = 0;

    if (!require_field(c, obj, "format", format, &v)) return false;
    if (!get_string(c, v, "format", &f)) return false;
    if (!str_eq_lit(f, format))
        return fail(c, "format: expected '%s', got '" STR_FMT "'", format, STR_ARG(f));
    if (!require_field(c, obj, "format_version", format, &v)) return false;
    if (!get_int(c, v, "format_version", &version)) return false;
    if (version != 0) return fail(c, "format_version: unsupported version %lld", (long long)version);
    return true;
}

static bool find_name(const Str *names, size_t n, Str want, uint32_t *out) {
    size_t i;
    for (i = 0; i < n; i++) {
        if (str_eq(names[i], want)) {
            *out = (uint32_t)i;
            return true;
        }
    }
    return false;
}

static bool unique_names(Ctx *c, const Str *names, size_t n, const char *what) {
    size_t i, k;
    for (i = 0; i < n; i++)
        for (k = i + 1; k < n; k++)
            if (str_eq(names[i], names[k]))
                return fail(c, "duplicate name '" STR_FMT "' in %s", STR_ARG(names[i]), what);
    return true;
}

/* Any syntax error collapses to one message. The previous implementation
 * returned a single error from its JSON library. The specific reason stays in
 * `err` and is worth surfacing if these diagnostics ever stop needing to
 * match an oracle. */
static bool parse_root(Ctx *c, const char *bytes, size_t len, const JsonValue **out) {
    char err[256];
    JsonValue *v = json_parse(c->arena, bytes, len, err, sizeof err);
    if (v == NULL) return fail(c, "malformed JSON");
    if (v->kind != JSON_OBJECT) return fail(c, "top level is not an object");
    *out = v;
    return true;
}

/* --- program -------------------------------------------------------------- */

bool parse_program(Arena *a, const char *bytes, size_t len, IrProgram *out, Diag *diag) {
    Ctx ctx;
    Ctx *c = &ctx;
    const JsonValue *obj = NULL, *arr = NULL, *item = NULL;
    IrChannel *channels;
    IrFrame *frames;
    IrWaveform *waveforms;
    IrElement *elements;
    Str *chan_names, *frame_names, *wf_names;
    size_t i, k;

    static const char *const top[] = {"format", "format_version", "channels",
                                      "frames", "waveforms", "elements", NULL};
    static const char *const chan_fields[] = {"name", "unit", "sample_unit", NULL};
    static const char *const frame_fields[] = {"name", "channel", "frequency", "phase", NULL};
    static const char *const wf_const_fields[] = {"name", "kind", "amplitude", NULL};
    static const char *const wf_samples_fields[] = {"name", "kind", "full_scale", "i", "q", NULL};
    static const char *const el_barrier_fields[] = {"id", "kind", "frames", NULL};
    static const char *const el_play_fields[] = {"id", "kind", "frame", "waveform", "duration", NULL};
    static const char *const el_dur_fields[] = {"id", "kind", "frame", "duration", NULL};
    static const char *const el_phase_fields[] = {"id", "kind", "frame", "phase", NULL};
    static const char *const el_freq_fields[] = {"id", "kind", "frame", "frequency", NULL};

    ctx.arena = a;
    ctx.diag = diag;

    if (!parse_root(c, bytes, len, &obj)) return false;
    if (!known(c, obj, top, "program")) return false;
    if (!check_header(c, obj, "qconform-program")) return false;

    /* channels */
    if (!require_field(c, obj, "channels", "program", &item)) return false;
    if (!get_array(c, item, "channels", &arr)) return false;
    channels = arena_array(a, arr->as.array.len, sizeof *channels);
    chan_names = arena_array(a, arr->as.array.len, sizeof *chan_names);
    if ((channels == NULL || chan_names == NULL) && arr->as.array.len != 0)
        return fail(c, "out of memory");
    for (i = 0; i < arr->as.array.len; i++) {
        const JsonValue *co = NULL, *v = NULL;
        Str name = {NULL, 0};
        if (!get_object(c, arr->as.array.items[i], "channels[]", &co)) return false;
        if (!known(c, co, chan_fields, "channels[]")) return false;
        if (!require_field(c, co, "name", "channels[]", &v)) return false;
        if (!get_name(c, v, "channels[].name", &name)) return false;
        channels[i].name = name;
        if (!require_field_s(c, co, "unit", name, &v)) return false;
        if (!get_positive_rat(c, v, "channels[].unit", &channels[i].unit)) return false;
        v = json_get(co, "sample_unit");
        channels[i].has_sample_unit = (v != NULL);
        if (v != NULL && !get_positive_rat(c, v, "channels[].sample_unit", &channels[i].sample_unit))
            return false;
        chan_names[i] = name;
    }
    out->channels = channels;
    out->n_channels = arr->as.array.len;
    if (!unique_names(c, chan_names, out->n_channels, "channels")) return false;

    /* frames */
    if (!require_field(c, obj, "frames", "program", &item)) return false;
    if (!get_array(c, item, "frames", &arr)) return false;
    frames = arena_array(a, arr->as.array.len, sizeof *frames);
    frame_names = arena_array(a, arr->as.array.len, sizeof *frame_names);
    if ((frames == NULL || frame_names == NULL) && arr->as.array.len != 0)
        return fail(c, "out of memory");
    for (i = 0; i < arr->as.array.len; i++) {
        const JsonValue *fo = NULL, *v = NULL;
        Str name = {NULL, 0}, chan_name = {NULL, 0};
        if (!get_object(c, arr->as.array.items[i], "frames[]", &fo)) return false;
        if (!known(c, fo, frame_fields, "frames[]")) return false;
        if (!require_field(c, fo, "name", "frames[]", &v)) return false;
        if (!get_name(c, v, "frames[].name", &name)) return false;
        if (!require_field_s(c, fo, "channel", name, &v)) return false;
        if (!get_name(c, v, "frames[].channel", &chan_name)) return false;
        frames[i].name = name;
        if (!find_name(chan_names, out->n_channels, chan_name, &frames[i].channel))
            return fail(c, "frame '" STR_FMT "': dangling channel '" STR_FMT "'",
                        STR_ARG(name), STR_ARG(chan_name));
        if (!require_field_s(c, fo, "frequency", name, &v)) return false;
        if (!get_rat(c, v, "frames[].frequency", &frames[i].frequency)) return false;
        if (!require_field_s(c, fo, "phase", name, &v)) return false;
        if (!get_rat(c, v, "frames[].phase", &frames[i].phase)) return false;
        frame_names[i] = name;
    }
    out->frames = frames;
    out->n_frames = arr->as.array.len;
    if (!unique_names(c, frame_names, out->n_frames, "frames")) return false;

    /* waveforms */
    if (!require_field(c, obj, "waveforms", "program", &item)) return false;
    if (!get_array(c, item, "waveforms", &arr)) return false;
    waveforms = arena_array(a, arr->as.array.len, sizeof *waveforms);
    wf_names = arena_array(a, arr->as.array.len, sizeof *wf_names);
    if ((waveforms == NULL || wf_names == NULL) && arr->as.array.len != 0)
        return fail(c, "out of memory");
    for (i = 0; i < arr->as.array.len; i++) {
        const JsonValue *wo = NULL, *v = NULL;
        Str name = {NULL, 0}, kind = {NULL, 0};
        if (!get_object(c, arr->as.array.items[i], "waveforms[]", &wo)) return false;
        if (!require_field(c, wo, "name", "waveforms[]", &v)) return false;
        if (!get_name(c, v, "waveforms[].name", &name)) return false;
        if (!require_field_s(c, wo, "kind", name, &v)) return false;
        if (!get_string(c, v, "waveforms[].kind", &kind)) return false;
        waveforms[i].name = name;
        if (str_eq_lit(kind, "const")) {
            if (!known(c, wo, wf_const_fields, "waveforms[]")) return false;
            waveforms[i].kind = WF_CONST;
            if (!require_field_s(c, wo, "amplitude", name, &v)) return false;
            if (!get_rat(c, v, "waveforms[].amplitude", &waveforms[i].as.constant.amplitude))
                return false;
        } else if (str_eq_lit(kind, "samples")) {
            const JsonValue *iv = NULL, *qv = NULL;
            int64_t *is, *qs;
            if (!known(c, wo, wf_samples_fields, "waveforms[]")) return false;
            waveforms[i].kind = WF_SAMPLES;
            if (!require_field_s(c, wo, "full_scale", name, &v)) return false;
            if (!get_int(c, v, "waveforms[].full_scale", &waveforms[i].as.samples.full_scale))
                return false;
            if (waveforms[i].as.samples.full_scale <= 0)
                return fail(c, "waveform '" STR_FMT "': full_scale must be > 0", STR_ARG(name));
            if (!require_field_s(c, wo, "i", name, &v)) return false;
            if (!get_array(c, v, "waveforms[].i", &iv)) return false;
            if (!require_field_s(c, wo, "q", name, &v)) return false;
            if (!get_array(c, v, "waveforms[].q", &qv)) return false;
            if (iv->as.array.len != qv->as.array.len)
                return fail(c, "waveform '" STR_FMT "': i/q length mismatch", STR_ARG(name));
            is = arena_array(a, iv->as.array.len, sizeof *is);
            qs = arena_array(a, qv->as.array.len, sizeof *qs);
            if ((is == NULL || qs == NULL) && iv->as.array.len != 0)
                return fail(c, "out of memory");
            for (k = 0; k < iv->as.array.len; k++)
                if (!get_int(c, iv->as.array.items[k], "waveforms[].i[]", &is[k])) return false;
            for (k = 0; k < qv->as.array.len; k++)
                if (!get_int(c, qv->as.array.items[k], "waveforms[].q[]", &qs[k])) return false;
            waveforms[i].as.samples.i = is;
            waveforms[i].as.samples.q = qs;
            waveforms[i].as.samples.len = iv->as.array.len;
        } else {
            return fail(c, "waveform '" STR_FMT "': unknown kind '" STR_FMT "'",
                        STR_ARG(name), STR_ARG(kind));
        }
        wf_names[i] = name;
    }
    out->waveforms = waveforms;
    out->n_waveforms = arr->as.array.len;
    if (!unique_names(c, wf_names, out->n_waveforms, "waveforms")) return false;

    /* elements */
    if (!require_field(c, obj, "elements", "program", &item)) return false;
    if (!get_array(c, item, "elements", &arr)) return false;
    elements = arena_array(a, arr->as.array.len, sizeof *elements);
    if (elements == NULL && arr->as.array.len != 0) return fail(c, "out of memory");
    for (i = 0; i < arr->as.array.len; i++) {
        const JsonValue *eo = NULL, *v = NULL;
        Str kind = {NULL, 0}, fname = {NULL, 0};
        int64_t id = 0;
        uint32_t frame = 0;

        if (!get_object(c, arr->as.array.items[i], "elements[]", &eo)) return false;
        if (!require_field(c, eo, "id", "elements[]", &v)) return false;
        if (!get_int(c, v, "elements[].id", &id)) return false;
        if (id < 0) return fail(c, "element id %lld: negative", (long long)id);
        if (!require_field(c, eo, "kind", "elements[]", &v)) return false;
        if (!get_string(c, v, "elements[].kind", &kind)) return false;
        elements[i].id = id;

        if (str_eq_lit(kind, "barrier")) {
            if (!known(c, eo, el_barrier_fields, "elements[]")) return false;
            elements[i].kind = EL_BARRIER;
            elements[i].as.barrier.frames = NULL;
            elements[i].as.barrier.len = 0;
            v = json_get(eo, "frames");
            if (v != NULL) {
                const JsonValue *fa = NULL;
                uint32_t *refs;
                if (!get_array(c, v, "barrier.frames", &fa)) return false;
                refs = arena_array(a, fa->as.array.len, sizeof *refs);
                if (refs == NULL && fa->as.array.len != 0) return fail(c, "out of memory");
                for (k = 0; k < fa->as.array.len; k++) {
                    Str fn;
                    if (!get_name(c, fa->as.array.items[k], "barrier.frames[]", &fn)) return false;
                    if (!find_name(frame_names, out->n_frames, fn, &refs[k]))
                        return fail(c, "element %lld: dangling frame '" STR_FMT "'",
                                    (long long)id, STR_ARG(fn));
                }
                elements[i].as.barrier.frames = refs;
                elements[i].as.barrier.len = fa->as.array.len;
            }
            continue;
        }

        if (!require_field(c, eo, "frame", "elements[]", &v)) return false;
        if (!get_name(c, v, "elements[].frame", &fname)) return false;
        if (!find_name(frame_names, out->n_frames, fname, &frame))
            return fail(c, "element %lld: dangling frame '" STR_FMT "'", (long long)id, STR_ARG(fname));

        if (str_eq_lit(kind, "play")) {
            Str wname = {NULL, 0};
            uint32_t wf = 0;
            if (!known(c, eo, el_play_fields, "elements[]")) return false;
            if (!require_field(c, eo, "waveform", "play", &v)) return false;
            if (!get_name(c, v, "play.waveform", &wname)) return false;
            if (!find_name(wf_names, out->n_waveforms, wname, &wf))
                return fail(c, "element %lld: dangling waveform '" STR_FMT "'",
                            (long long)id, STR_ARG(wname));
            if (waveforms[wf].kind == WF_SAMPLES && !channels[frames[frame].channel].has_sample_unit)
                return fail(c, "element %lld: samples waveform on channel '" STR_FMT
                               "' with no sample_unit",
                            (long long)id, STR_ARG(channels[frames[frame].channel].name));
            elements[i].kind = EL_PLAY;
            elements[i].as.play.frame = frame;
            elements[i].as.play.waveform = wf;
            if (!require_field(c, eo, "duration", "play", &v)) return false;
            if (!get_int(c, v, "play.duration", &elements[i].as.play.duration)) return false;
        } else if (str_eq_lit(kind, "capture") || str_eq_lit(kind, "delay")) {
            int64_t dur = 0;
            char kindbuf[16];
            size_t n = kind.len < sizeof kindbuf - 1 ? kind.len : sizeof kindbuf - 1;
            memcpy(kindbuf, kind.ptr, n);
            kindbuf[n] = '\0';
            if (!known(c, eo, el_dur_fields, "elements[]")) return false;
            if (!require_field(c, eo, "duration", kindbuf, &v)) return false;
            if (!get_int(c, v, "duration", &dur)) return false;
            if (kind.ptr[0] == 'c') {
                elements[i].kind = EL_CAPTURE;
                elements[i].as.capture.frame = frame;
                elements[i].as.capture.duration = dur;
            } else {
                elements[i].kind = EL_DELAY;
                elements[i].as.delay.frame = frame;
                elements[i].as.delay.duration = dur;
            }
        } else if (str_eq_lit(kind, "shift_phase")) {
            if (!known(c, eo, el_phase_fields, "elements[]")) return false;
            elements[i].kind = EL_SHIFT_PHASE;
            elements[i].as.shift_phase.frame = frame;
            if (!require_field(c, eo, "phase", "shift_phase", &v)) return false;
            if (!get_rat(c, v, "shift_phase.phase", &elements[i].as.shift_phase.phase)) return false;
        } else if (str_eq_lit(kind, "set_frequency")) {
            if (!known(c, eo, el_freq_fields, "elements[]")) return false;
            elements[i].kind = EL_SET_FREQUENCY;
            elements[i].as.set_frequency.frame = frame;
            if (!require_field(c, eo, "frequency", "set_frequency", &v)) return false;
            if (!get_rat(c, v, "set_frequency.frequency", &elements[i].as.set_frequency.frequency))
                return false;
        } else {
            return fail(c, "element %lld: unknown kind '" STR_FMT "'", (long long)id, STR_ARG(kind));
        }
    }
    out->elements = elements;
    out->n_elements = arr->as.array.len;

    for (i = 0; i < out->n_elements; i++)
        for (k = i + 1; k < out->n_elements; k++)
            if (elements[i].id == elements[k].id)
                return fail(c, "duplicate element id %lld", (long long)elements[i].id);

    return true;
}

/* --- descriptor ----------------------------------------------------------- */

static bool parse_constraint(Ctx *c, const JsonValue *cv, Constraint *out) {
    const JsonValue *co = NULL, *v = NULL;
    static const char *const fields[] = {"id",         "quantity", "shape",      "severity",
                                         "min_units",  "max_units", "min",       "max",
                                         "resolution", "post_mixer", "grid",     "evidence",
                                         NULL};

    if (!get_object(c, cv, "constraints[]", &co)) return false;
    if (!known(c, co, fields, "constraints[]")) return false;

    if (!require_field(c, co, "id", "constraints[]", &v)) return false;
    if (!get_rule_id(c, v, "constraint.id", &out->id)) return false;
    if (!require_field(c, co, "quantity", "constraints[]", &v)) return false;
    if (!get_quantity(c, v, "constraint.quantity", &out->quantity)) return false;
    if (!require_field(c, co, "shape", "constraints[]", &v)) return false;
    if (!get_shape(c, v, "constraint.shape", &out->shape)) return false;
    if (!require_field(c, co, "severity", "constraints[]", &v)) return false;
    if (!get_severity(c, v, "constraint.severity", &out->severity)) return false;

    out->has_min_units = out->has_max_units = false;
    out->has_min = out->has_max = out->has_resolution = false;
    out->has_grid = false;
    out->post_mixer = false;

    if ((v = json_get(co, "min_units")) != NULL) {
        if (!get_int(c, v, "constraint.min_units", &out->min_units)) return false;
        out->has_min_units = true;
    }
    if ((v = json_get(co, "max_units")) != NULL) {
        if (!get_int(c, v, "constraint.max_units", &out->max_units)) return false;
        out->has_max_units = true;
    }
    if ((v = json_get(co, "min")) != NULL) {
        if (!get_rat(c, v, "constraint.min", &out->min)) return false;
        out->has_min = true;
    }
    if ((v = json_get(co, "max")) != NULL) {
        if (!get_rat(c, v, "constraint.max", &out->max)) return false;
        out->has_max = true;
    }
    if ((v = json_get(co, "resolution")) != NULL) {
        if (!get_rat(c, v, "constraint.resolution", &out->resolution)) return false;
        out->has_resolution = true;
    }
    if ((v = json_get(co, "post_mixer")) != NULL) {
        if (!get_bool(c, v, "constraint.post_mixer", &out->post_mixer)) return false;
    }
    if ((v = json_get(co, "grid")) != NULL) {
        if (!get_int(c, v, "constraint.grid", &out->grid)) return false;
        out->has_grid = true;
    }
    /* evidence is authoring-side: type-check the container, drop the content */
    if ((v = json_get(co, "evidence")) != NULL) {
        const JsonValue *ignored;
        if (!get_array(c, v, "constraint.evidence", &ignored)) return false;
    }
    return true;
}

bool parse_descriptor(Arena *a, const char *bytes, size_t len, Descriptor *out, Diag *diag) {
    Ctx ctx;
    Ctx *c = &ctx;
    const JsonValue *obj = NULL, *arr = NULL, *item = NULL, *io = NULL, *lo = NULL, *ro = NULL;
    CapChannel *channels;
    Budget *budgets;
    Str *names;
    size_t i, k;

    static const char *const top[] = {"format",  "format_version", "identification", "frames",
                                      "rounding", "channels",      "budgets",        NULL};
    static const char *const ident_fields[] = {"name",    "board",   "fw_timestamp", "cfg_sw_version",
                                               "library", "descriptor_version", NULL};
    static const char *const lib_fields[] = {"name", "version", NULL};
    static const char *const rounding_fields[] = {"time", "frequency", "phase", "amplitude", NULL};
    static const char *const chan_fields[] = {"name",         "kind",          "vendor_type",
                                              "unit",         "sample_unit",   "duration_grid",
                                              "schedule_grid", "capabilities", "constraints",
                                              "vendor_behavior", NULL};
    static const char *const cap_fields[] = {"phrst", "n_tones", "envelope_memory_samples",
                                             "envelope_sample_grid", "envelope_max_abs", NULL};
    static const char *const budget_fields[] = {"id", "limit", "cost_model", "evidence", NULL};
    static const char *const cost_fields[] = {"kind", "per_item", "overhead", "reserved_min",
                                              "reserved_max", NULL};

    ctx.arena = a;
    ctx.diag = diag;

    if (!parse_root(c, bytes, len, &obj)) return false;
    if (!known(c, obj, top, "descriptor")) return false;
    if (!check_header(c, obj, "qconform-descriptor")) return false;

    /* identification */
    if (!require_field(c, obj, "identification", "descriptor", &item)) return false;
    if (!get_object(c, item, "identification", &io)) return false;
    if (!known(c, io, ident_fields, "identification")) return false;
    if (!require_field(c, io, "library", "identification", &item)) return false;
    if (!get_object(c, item, "library", &lo)) return false;
    if (!known(c, lo, lib_fields, "library")) return false;

    if (!require_field(c, io, "name", "identification", &item)) return false;
    if (!get_name(c, item, "identification.name", &out->ident.name)) return false;
    if (!require_field(c, io, "board", "identification", &item)) return false;
    if (!get_string(c, item, "identification.board", &out->ident.board)) return false;
    if (!require_field(c, io, "fw_timestamp", "identification", &item)) return false;
    if (!get_string(c, item, "identification.fw_timestamp", &out->ident.fw_timestamp)) return false;
    if (!require_field(c, io, "cfg_sw_version", "identification", &item)) return false;
    if (!get_string(c, item, "identification.cfg_sw_version", &out->ident.cfg_sw_version)) return false;
    if (!require_field(c, lo, "name", "library", &item)) return false;
    if (!get_string(c, item, "library.name", &out->ident.library_name)) return false;
    if (!require_field(c, lo, "version", "library", &item)) return false;
    if (!get_string(c, item, "library.version", &out->ident.library_version)) return false;
    if (!require_field(c, io, "descriptor_version", "identification", &item)) return false;
    if (!get_string(c, item, "identification.descriptor_version", &out->ident.descriptor_version))
        return false;

    /* rounding */
    if (!require_field(c, obj, "rounding", "descriptor", &item)) return false;
    if (!get_object(c, item, "rounding", &ro)) return false;
    if (!known(c, ro, rounding_fields, "rounding")) return false;
    if (!require_field(c, ro, "time", "rounding", &item)) return false;
    if (!get_round_mode(c, item, "rounding.time", &out->rounding.time)) return false;
    if (!require_field(c, ro, "frequency", "rounding", &item)) return false;
    if (!get_round_mode(c, item, "rounding.frequency", &out->rounding.frequency)) return false;
    if (!require_field(c, ro, "phase", "rounding", &item)) return false;
    if (!get_round_mode(c, item, "rounding.phase", &out->rounding.phase)) return false;
    if (!require_field(c, ro, "amplitude", "rounding", &item)) return false;
    if (!get_round_mode(c, item, "rounding.amplitude", &out->rounding.amplitude)) return false;

    /* channels */
    if (!require_field(c, obj, "channels", "descriptor", &item)) return false;
    if (!get_array(c, item, "channels", &arr)) return false;
    channels = arena_array(a, arr->as.array.len, sizeof *channels);
    names = arena_array(a, arr->as.array.len, sizeof *names);
    if ((channels == NULL || names == NULL) && arr->as.array.len != 0)
        return fail(c, "out of memory");
    for (i = 0; i < arr->as.array.len; i++) {
        const JsonValue *co = NULL, *v = NULL, *capo = NULL, *cons_arr = NULL;
        Constraint *cons;
        Str name = {NULL, 0};
        Capabilities caps;

        if (!get_object(c, arr->as.array.items[i], "channels[]", &co)) return false;
        if (!known(c, co, chan_fields, "channels[]")) return false;
        if (!require_field(c, co, "name", "channels[]", &v)) return false;
        if (!get_name(c, v, "channels[].name", &name)) return false;

        memset(&caps, 0, sizeof caps);
        if ((v = json_get(co, "capabilities")) != NULL) {
            if (!get_object(c, v, "capabilities", &capo)) return false;
            if (!known(c, capo, cap_fields, "capabilities")) return false;
            if ((v = json_get(capo, "phrst")) != NULL) {
                if (!get_bool(c, v, "capabilities.phrst", &caps.phrst)) return false;
                caps.has_phrst = true;
            }
            if ((v = json_get(capo, "n_tones")) != NULL) {
                if (!get_int(c, v, "capabilities.n_tones", &caps.n_tones)) return false;
                caps.has_n_tones = true;
            }
            if ((v = json_get(capo, "envelope_memory_samples")) != NULL) {
                if (!get_int(c, v, "capabilities.envelope_memory_samples",
                             &caps.envelope_memory_samples))
                    return false;
                caps.has_envelope_memory_samples = true;
            }
            if ((v = json_get(capo, "envelope_sample_grid")) != NULL) {
                if (!get_int(c, v, "capabilities.envelope_sample_grid", &caps.envelope_sample_grid))
                    return false;
                caps.has_envelope_sample_grid = true;
            }
            if ((v = json_get(capo, "envelope_max_abs")) != NULL) {
                if (!get_int(c, v, "capabilities.envelope_max_abs", &caps.envelope_max_abs))
                    return false;
                caps.has_envelope_max_abs = true;
            }
        }

        if (!require_field_s(c, co, "constraints", name, &v)) return false;
        if (!get_array(c, v, "constraints", &cons_arr)) return false;
        cons = arena_array(a, cons_arr->as.array.len, sizeof *cons);
        if (cons == NULL && cons_arr->as.array.len != 0) return fail(c, "out of memory");
        for (k = 0; k < cons_arr->as.array.len; k++)
            if (!parse_constraint(c, cons_arr->as.array.items[k], &cons[k])) return false;

        /* vendor_behavior documents the toolchain for report readers; the
         * checker encodes its consequences as rules, so only the container
         * shape is validated here */
        if ((v = json_get(co, "vendor_behavior")) != NULL) {
            const JsonValue *ignored;
            if (!get_array(c, v, "vendor_behavior", &ignored)) return false;
        }

        channels[i].name = name;
        if (!require_field_s(c, co, "kind", name, &v)) return false;
        if (!get_channel_kind(c, v, "channels[].kind", &channels[i].kind)) return false;
        if (!require_field_s(c, co, "vendor_type", name, &v)) return false;
        if (!get_string(c, v, "channels[].vendor_type", &channels[i].vendor_type)) return false;
        if (!require_field_s(c, co, "unit", name, &v)) return false;
        if (!get_positive_rat(c, v, "channels[].unit", &channels[i].unit)) return false;
        v = json_get(co, "sample_unit");
        channels[i].has_sample_unit = (v != NULL);
        if (v != NULL && !get_positive_rat(c, v, "channels[].sample_unit", &channels[i].sample_unit))
            return false;
        if (!require_field_s(c, co, "duration_grid", name, &v)) return false;
        if (!get_int(c, v, "channels[].duration_grid", &channels[i].duration_grid)) return false;
        if (!require_field_s(c, co, "schedule_grid", name, &v)) return false;
        if (!get_int(c, v, "channels[].schedule_grid", &channels[i].schedule_grid)) return false;
        channels[i].capabilities = caps;
        channels[i].constraints = cons;
        channels[i].n_constraints = cons_arr->as.array.len;
        names[i] = name;
    }
    out->channels = channels;
    out->n_channels = arr->as.array.len;
    if (!unique_names(c, names, out->n_channels, "descriptor channels")) return false;

    /* budgets */
    if (!require_field(c, obj, "budgets", "descriptor", &item)) return false;
    if (!get_array(c, item, "budgets", &arr)) return false;
    budgets = arena_array(a, arr->as.array.len, sizeof *budgets);
    if (budgets == NULL && arr->as.array.len != 0) return fail(c, "out of memory");
    for (i = 0; i < arr->as.array.len; i++) {
        const JsonValue *bo = NULL, *v = NULL, *cmo = NULL;
        CostModel cm;

        if (!get_object(c, arr->as.array.items[i], "budgets[]", &bo)) return false;
        if (!known(c, bo, budget_fields, "budgets[]")) return false;
        if (!require_field(c, bo, "cost_model", "budgets[]", &v)) return false;
        if (!get_object(c, v, "cost_model", &cmo)) return false;
        if (!known(c, cmo, cost_fields, "cost_model")) return false;

        memset(&cm, 0, sizeof cm);
        cm.per_item = 1;
        cm.overhead = 0;
        if (!require_field(c, cmo, "kind", "cost_model", &v)) return false;
        if (!get_cost_kind(c, v, "cost_model.kind", &cm.kind)) return false;
        if ((v = json_get(cmo, "per_item")) != NULL)
            if (!get_int(c, v, "cost_model.per_item", &cm.per_item)) return false;
        if ((v = json_get(cmo, "overhead")) != NULL)
            if (!get_int(c, v, "cost_model.overhead", &cm.overhead)) return false;
        if ((v = json_get(cmo, "reserved_min")) != NULL) {
            if (!get_int(c, v, "cost_model.reserved_min", &cm.reserved_min)) return false;
            cm.has_reserved_min = true;
        }
        if ((v = json_get(cmo, "reserved_max")) != NULL) {
            if (!get_int(c, v, "cost_model.reserved_max", &cm.reserved_max)) return false;
            cm.has_reserved_max = true;
        }
        if ((v = json_get(bo, "evidence")) != NULL) {
            const JsonValue *ignored;
            if (!get_array(c, v, "budget.evidence", &ignored)) return false;
        }

        if (!require_field(c, bo, "id", "budgets[]", &v)) return false;
        if (!get_budget_id(c, v, "budget.id", &budgets[i].id)) return false;
        if (!require_field(c, bo, "limit", "budgets[]", &v)) return false;
        if (!get_int(c, v, "budget.limit", &budgets[i].limit)) return false;
        budgets[i].cost = cm;
    }
    out->budgets = budgets;
    out->n_budgets = arr->as.array.len;

    if (!require_field(c, obj, "frames", "descriptor", &item)) return false;
    if (!get_frames_mode(c, item, "frames", &out->frames)) return false;

    return true;
}
