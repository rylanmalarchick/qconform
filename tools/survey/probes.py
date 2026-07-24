"""Probe definitions for the asm_v2 constraint survey.

Every probe list is derived deterministically from config values (no RNG,
no clocks). A probe is a dict: axis, kind, target channel, parameter
overrides, and a note naming the constraint candidate it exercises.
"""


def gen_classes(soccfg):
    """Unique generator behavior classes, one representative channel each,
    plus one duplicate channel per class (if any) for config-dependence
    cross-checks."""
    classes = {}
    for ch, g in enumerate(soccfg['gens']):
        key = (g['type'], g.get('maxlen'), g['f_fabric'], g['f_dds'],
               g['interpolation'], g['maxv'])
        classes.setdefault(key, []).append(ch)
    out = []
    for key, chs in classes.items():
        out.append({'ch': chs[0], 'dup_ch': chs[1] if len(chs) > 1 else None,
                    'gcfg': soccfg['gens'][chs[0]]})
    return out


def length_values(f_fabric, maxlen_cycles=2**16):
    """Pulse lengths in us hitting boundaries and rounding ties."""
    cyc = lambda c: c / f_fabric
    vals = []
    for c, note in [
            (-599, 'negative length'),
            (0, 'zero length'),
            (1, 'below min 3 cycles'),
            (2, 'below min 3 cycles'),
            (3, 'min boundary'),
            (4, 'just above min'),
            (60, 'nominal'),
            (maxlen_cycles - 1, 'max boundary'),
            (maxlen_cycles, 'first over max'),
            (maxlen_cycles + 1, 'over max')]:
        vals.append((cyc(c), note))
    # rounding ties: n+0.5 cycles; half-even rounds 10.5->10, 11.5->12
    for n in (10, 11, 12, 13):
        vals.append((cyc(n + 0.5), f'tie {n}+0.5 cycles'))
    for n, frac in ((20, 0.25), (20, 0.75), (21, 0.25), (21, 0.75)):
        vals.append((cyc(n + frac), f'fraction {n}+{frac} cycles'))
    vals.append((0.1003, 'phase-0 reference case'))
    return vals


def freq_values(gcfg):
    """Frequencies in MHz probing the DDS range and quantization."""
    f_dds = gcfg['f_dds']
    fstep = f_dds / 2**gcfg['b_dds']
    vals = [
        (0.0, 'zero'),
        (f_dds / 4, 'mid band'),
        (f_dds / 2 - 1.0, 'near upper edge'),
        (f_dds / 2, 'upper edge'),
        (f_dds / 2 + 1.0, 'just over upper edge'),
        (-f_dds / 2, 'lower edge'),
        (-f_dds / 2 - 1.0, 'just under lower edge'),
        (f_dds, 'full f_dds'),
        (1.5 * f_dds, '1.5x f_dds'),
        (100.0 + 0.3 * fstep, 'quantization +0.3 step'),
        (100.0 + 0.5 * fstep, 'quantization +0.5 step'),
        (100.0 + 0.7 * fstep, 'quantization +0.7 step'),
    ]
    return vals


def gain_values(maxv):
    """Gains (relative full-scale) probing bounds and truncation mode."""
    return [
        (0.5, 'nominal'),
        (1.0, 'full scale'),
        (-1.0, 'negative full scale'),
        (1.001, 'just over full scale'),
        (1.5, 'well over full scale'),
        (-1.5, 'well under negative full scale'),
        (0.0, 'zero'),
        # truncation probes: request maxv*g with fractional raw values
        ((int(0.5 * maxv) + 0.4) / maxv, 'raw +0.4 (trunc vs round)'),
        ((int(0.5 * maxv) + 0.6) / maxv, 'raw +0.6 (trunc vs round)'),
        (-(int(0.5 * maxv) + 0.6) / maxv, 'raw -0.6 (trunc toward zero)'),
    ]


def phase_values(b_phase):
    """Phases in degrees probing wrap and resolution rounding."""
    pstep = 360.0 / 2**b_phase
    return [
        (0.0, 'zero'),
        (90.0, 'nominal'),
        (359.999, 'near full turn'),
        (360.0, 'full turn (wrap)'),
        (720.0, 'two turns (wrap)'),
        (-90.0, 'negative'),
        (45.0 + 0.3 * pstep, 'quantization +0.3 step'),
        (45.0 + 0.5 * pstep, 'quantization +0.5 step'),
        (45.0 + 0.7 * pstep, 'quantization +0.7 step'),
    ]


def build_probes(soccfg, synthetic_variants=True):
    probes = []
    tproc = soccfg['tprocs'][0]

    for cls in gen_classes(soccfg):
        ch, gcfg = cls['ch'], cls['gcfg']
        gt = gcfg['type']
        mux = 'mux' in gt
        base = {'gen_ch': ch, 'gen_type': gt, 'dup_ch': cls['dup_ch']}

        maxcyc = 2**32 if mux else 2**16
        for v, note in length_values(gcfg['f_fabric'], maxcyc):
            style = 'mux_const' if mux else 'const'
            probes.append({**base, 'axis': 'length', 'kind': style,
                           'param': 'length', 'requested': v, 'note': note})
        if cls['dup_ch'] is not None and not mux:
            probes.append({**base, 'gen_ch': cls['dup_ch'], 'axis': 'length',
                           'kind': 'const', 'param': 'length',
                           'requested': 60.5 / gcfg['f_fabric'],
                           'note': 'duplicate-channel cross-check (same class)'})

        if not mux:
            for v, note in freq_values(gcfg):
                probes.append({**base, 'axis': 'freq', 'kind': 'const',
                               'param': 'freq', 'requested': v, 'note': note})
            for v, note in gain_values(gcfg['maxv']):
                probes.append({**base, 'axis': 'gain', 'kind': 'const',
                               'param': 'gain', 'requested': v, 'note': note})
            for v, note in phase_values(gcfg['b_phase']):
                probes.append({**base, 'axis': 'phase', 'kind': 'const',
                               'param': 'phase', 'requested': v, 'note': note})
            probes.append({**base, 'axis': 'phrst', 'kind': 'const',
                           'param': 'phrst', 'requested': 1,
                           'note': 'phrst support gated by gen type'})

        # envelope axes only where an envelope memory exists
        if 'maxlen' in gcfg and not mux:
            spc = gcfg['samps_per_clk']
            maxlen = gcfg['maxlen']
            for nsamp, note in [
                    (16 * spc, 'aligned envelope'),
                    (16 * spc + 1, 'length not multiple of samps_per_clk'),
                    (16 * spc - 1, 'length not multiple of samps_per_clk'),
                    (maxlen, 'envelope exactly fills memory'),
                    (maxlen + spc, 'envelope exceeds memory (suspected unchecked)')]:
                probes.append({**base, 'axis': 'envelope', 'kind': 'arb',
                               'param': 'env_samples', 'requested': nsamp,
                               'note': note})
            probes.append({**base, 'axis': 'envelope', 'kind': 'arb',
                           'param': 'env_maxv', 'requested': gcfg['maxv'] + 1,
                           'env_samples': 16 * spc,
                           'note': 'envelope amplitude over maxv'})
            probes.append({**base, 'axis': 'envelope', 'kind': 'env_two',
                           'param': 'env_samples',
                           'requested': (maxlen // 2 // spc * spc) + (maxlen // 2 // spc * spc),
                           'note': 'two envelopes jointly near memory size'})

        if mux:
            probes.append({**base, 'axis': 'mux', 'kind': 'mux_const',
                           'param': 'mask', 'requested': 8,
                           'note': 'mask tone index == n_tones (out of range)'})
            probes.append({**base, 'axis': 'phrst', 'kind': 'mux_const',
                           'param': 'phrst', 'requested': 1,
                           'note': 'phrst on mux gen (expect unsupported)'})

    # whole-program budgets (config-level, use first standard gen)
    std_ch = next(c['ch'] for c in gen_classes(soccfg) if 'mux' not in c['gcfg']['type'])
    for n in (8, tproc['wmem_size'] - 8, tproc['wmem_size'] + 8):
        probes.append({'axis': 'budget_wmem', 'kind': 'many_waveforms',
                       'gen_ch': std_ch, 'param': 'n_waveforms', 'requested': n,
                       'note': f'waveform memory {tproc["wmem_size"]} words'})
    for n in (100, 2000, 8000, 20000):
        probes.append({'axis': 'budget_pmem', 'kind': 'many_instructions',
                       'gen_ch': std_ch, 'param': 'n_pulses', 'requested': n,
                       'note': f'program memory {tproc["pmem_size"]} words'})
    for n in (2, 8, 12, 16, 24):
        probes.append({'axis': 'budget_regs', 'kind': 'many_loops',
                       'gen_ch': std_ch, 'param': 'n_loops', 'requested': n,
                       'note': f'data registers dreg_qty {tproc["dreg_qty"]}'})

    # readout config lengths on the first dynamic readout, if any
    dyn_ro = next((i for i, r in enumerate(soccfg['readouts'])
                   if 'tproc_ctrl' in r), None)
    if dyn_ro is not None:
        rocfg = soccfg['readouts'][dyn_ro]
        f_out = rocfg['f_output']
        for c, note in [(2, 'below min 3'), (3, 'min boundary'),
                        (2**16 - 1, 'max boundary'), (2**16, 'first over max')]:
            probes.append({'axis': 'readout', 'kind': 'ro_config',
                           'ro_ch': dyn_ro, 'gen_ch': std_ch,
                           'param': 'length', 'requested': c / f_out,
                           'note': note})

    # timing
    for t, note in [(-0.1, 'negative pulse time'),
                    (10.5 / soccfg['gens'][std_ch]['f_fabric'], 'tie 10.5 cycles'),
                    (1e7, 'very large pulse time'),
                    (2**31 / (tproc['f_time']), 'wait over 31-bit cycles')]:
        kind = 'wait_long' if note.startswith('wait') else 'pulse_at_t'
        probes.append({'axis': 'timing', 'kind': kind, 'gen_ch': std_ch,
                       'param': 't', 'requested': t, 'note': note})

    # out-of-range channel index
    probes.append({'axis': 'channels', 'kind': 'const', 'gen_ch': len(soccfg['gens']),
                   'gen_type': 'out_of_range', 'param': 'length',
                   'requested': 0.1, 'note': 'generator index out of range'})

    # synthetic config variant: unsupported tproc revision
    if synthetic_variants:
        probes.append({'axis': 'config', 'kind': 'bad_revision', 'gen_ch': std_ch,
                       'param': 'revision', 'requested': 20,
                       'note': 'tproc revision outside ASM_REVISIONS (synthetic config)'})

    return probes
