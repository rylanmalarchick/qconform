"""Black-box probe runner: feed programs to asm_v2, record what it does.

Usage: python runner.py configs/<name>.json [outdir]

For each probe from probes.py, builds a fresh AveragerProgramV2 against
the config, compiles with no hardware, and classifies the outcome:
  accept        compiled; observed == requested (within readback epsilon)
  accept_round  compiled; observed != requested (silent quantization/repair)
  reject        RuntimeError/ValueError raised by a qick check
  crash         any other exception (unhelpful failure mode)
Rows go to <outdir>/<config>__<axis>.jsonl, deterministic and rerunnable:
no timestamps, no RNG; identical inputs give byte-identical files.
"""

import copy
import json
import logging
import re
import sys
from pathlib import Path

import numpy as np
import qick
from qick.qick_asm import QickConfig
from qick.asm_v2 import AveragerProgramV2

from probes import build_probes

READBACK_EPS = 0.0    # exact float compare; any quantization counts as a round


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        # vendor warnings embed object reprs; strip addresses for determinism
        self.records.append(re.sub(r'0x[0-9a-f]+', '0x_', record.getMessage()))


def declare_kwargs(soccfg, ch):
    g = soccfg['gens'][ch]
    kw = {'ch': ch, 'nqz': 1}
    if g.get('has_mixer'):
        kw['mixer_freq'] = g['f_dds'] / 4
    if 'mux' in g['type']:
        kw['mux_freqs'] = [g['f_dds'] / 8, g['f_dds'] / 16]
        if g.get('has_gain'):
            kw['mux_gains'] = [0.5, 0.4]
        if g.get('has_phase'):
            kw['mux_phases'] = [0.0, 0.0]
    return kw


class ProbeProgram(AveragerProgramV2):
    """One probe, one program. cfg carries the probe dict."""

    def _initialize(self, cfg):
        p = cfg['probe']
        soccfg = self.soccfg
        kind = p['kind']
        ch = p['gen_ch']
        gcfg = soccfg['gens'][ch] if ch < len(soccfg['gens']) else {'f_fabric': 100.0}
        nominal_len = 60 / gcfg.get('f_fabric', 100.0)

        if kind in ('const', 'pulse_at_t', 'wait_long'):
            self.declare_gen(**declare_kwargs(soccfg, ch))
            pulse = {'style': 'const', 'freq': 100.0, 'phase': 0.0,
                     'gain': 0.5, 'length': nominal_len}
            if kind == 'const' and p['param'] != 't':
                pulse[p['param']] = p['requested']
            self.add_pulse(ch=ch, name='p', **pulse)

        elif kind == 'mux_const':
            self.declare_gen(**declare_kwargs(soccfg, ch))
            pulse = {'style': 'const', 'mask': [0, 1], 'length': nominal_len}
            if p['param'] == 'length':
                pulse['length'] = p['requested']
            elif p['param'] == 'mask':
                pulse['mask'] = [p['requested']]
            elif p['param'] == 'phrst':
                pulse['phrst'] = p['requested']
            self.add_pulse(ch=ch, name='p', **pulse)

        elif kind in ('arb', 'env_two'):
            self.declare_gen(**declare_kwargs(soccfg, ch))
            spc = gcfg['samps_per_clk']
            if kind == 'arb':
                nsamp = int(p.get('env_samples', p['requested']))
                amp = p['requested'] if p['param'] == 'env_maxv' else gcfg['maxv'] // 2
                idata = np.full(max(nsamp, 0), int(amp), dtype=np.int32)
                qdata = np.zeros(max(nsamp, 0), dtype=np.int32) if gcfg['complex_env'] else None
                self.add_envelope(ch=ch, name='e', idata=idata, qdata=qdata)
                self.add_pulse(ch=ch, name='p', style='arb', envelope='e',
                               freq=100.0, phase=0.0, gain=0.5)
            else:
                half = gcfg['maxlen'] // 2 // spc * spc
                for nm in ('e1', 'e2'):
                    idata = np.full(half, gcfg['maxv'] // 2, dtype=np.int32)
                    self.add_envelope(ch=ch, name=nm, idata=idata,
                                      qdata=np.zeros(half, dtype=np.int32) if gcfg['complex_env'] else None)
                self.add_pulse(ch=ch, name='p', style='arb', envelope='e1',
                               freq=100.0, phase=0.0, gain=0.5)
                self.add_pulse(ch=ch, name='p2', style='arb', envelope='e2',
                               freq=100.0, phase=0.0, gain=0.5)

        elif kind == 'many_waveforms':
            self.declare_gen(**declare_kwargs(soccfg, ch))
            f_fab = gcfg['f_fabric']
            for i in range(int(p['requested'])):
                self.add_pulse(ch=ch, name=f'p{i}', style='const', freq=100.0,
                               phase=0.0, gain=0.5, length=(10 + i % 32) / f_fab)

        elif kind in ('many_instructions', 'many_loops'):
            self.declare_gen(**declare_kwargs(soccfg, ch))
            self.add_pulse(ch=ch, name='p', style='const', freq=100.0,
                           phase=0.0, gain=0.5, length=nominal_len)
            if kind == 'many_loops':
                for i in range(int(p['requested'])):
                    self.add_loop(f'l{i}', 2)

        elif kind == 'ro_config':
            self.declare_gen(**declare_kwargs(soccfg, ch))
            self.add_pulse(ch=ch, name='p', style='const', freq=100.0,
                           phase=0.0, gain=0.5, length=nominal_len)
            ro = p['ro_ch']
            self.declare_readout(ch=ro, length=1.0)
            self.add_readoutconfig(ch=ro, name='rocfg', freq=100.0,
                                   length=p['requested'])

    def _body(self, cfg):
        p = cfg['probe']
        kind = p['kind']
        if kind == 'many_instructions':
            for _ in range(int(p['requested'])):
                self.pulse(ch=p['gen_ch'], name='p', t=0.0)
        elif kind == 'many_waveforms':
            self.pulse(ch=p['gen_ch'], name='p0', t=0.0)
        elif kind == 'pulse_at_t':
            self.pulse(ch=p['gen_ch'], name='p', t=p['requested'])
        elif kind == 'wait_long':
            self.pulse(ch=p['gen_ch'], name='p', t=0.0)
            self.wait(p['requested'])
        elif kind == 'ro_config':
            self.send_readoutconfig(ch=p['ro_ch'], name='rocfg', t=0.0)
            self.pulse(ch=p['gen_ch'], name='p', t=0.0)
            self.trigger(ros=[p['ro_ch']], t=0.0)
        elif kind == 'env_two':
            self.pulse(ch=p['gen_ch'], name='p', t=0.0)
            self.delay_auto()
            self.pulse(ch=p['gen_ch'], name='p2', t=0.0)
        else:
            self.pulse(ch=p['gen_ch'], name='p', t=0.0)


def observe(prog, probe):
    """Post-compile readback of the probed parameter."""
    obs = {}
    param = probe['param']
    if 'p' in prog.pulses and param in ('length', 'freq', 'phase', 'gain'):
        name = {'length': 'total_length'}.get(param, param)
        try:
            obs['readback'] = float(prog.get_pulse_param('p', name))
        except (KeyError, ValueError, TypeError):
            pass
        waves = prog.pulses['p'].waveforms
        if waves:
            w = waves[0]
            obs['wave_raw'] = {k: int(w[k]) for k in
                               ('freq', 'phase', 'gain', 'length')
                               if not hasattr(w[k], 'spans')}
    if probe['kind'] == 'many_waveforms':
        obs['n_waves'] = len(prog.waves)
    if probe['axis'] in ('budget_pmem', 'budget_regs', 'envelope'):
        if prog.binprog is not None:
            obs['pmem_words'] = int(len(prog.binprog['pmem']))
    if probe['axis'] == 'envelope':
        try:
            obs['env_next_addr'] = int(prog.envelopes[probe['gen_ch']]['next_addr'])
        except (KeyError, IndexError, TypeError):
            pass
    return obs


def classify(probe, obs):
    req = probe['requested']
    rb = obs.get('readback')
    if rb is None or probe['param'] in ('phrst', 'mask', 'n_waveforms',
                                        'n_pulses', 'n_loops', 'revision',
                                        'env_samples', 'env_maxv', 't'):
        return 'accept'
    if req == 0:
        return 'accept' if abs(rb) < 1e-12 else 'accept_round'
    return 'accept' if abs(rb - req) <= READBACK_EPS * abs(req) else 'accept_round'


def run_probe(soccfg, probe):
    cap = LogCapture()
    logging.getLogger().addHandler(cap)
    row = {'axis': probe['axis'], 'kind': probe['kind'],
           'gen_ch': probe.get('gen_ch'), 'gen_type': probe.get('gen_type'),
           'param': probe['param'], 'requested': probe['requested'],
           'note': probe['note']}
    try:
        if probe['kind'] == 'bad_revision':
            bad = copy.deepcopy(soccfg._cfg)
            bad['tprocs'][0]['revision'] = probe['requested']
            soccfg = QickConfig(bad)
        prog = ProbeProgram(soccfg, reps=1, final_delay=1.0, cfg={'probe': probe})
        prog.compile()
        obs = observe(prog, probe)
        row['outcome'] = classify(probe, obs)
        row['observed'] = obs
        if row['outcome'] == 'accept_round' and 'readback' in obs:
            row['delta'] = obs['readback'] - probe['requested']
    except (RuntimeError, ValueError) as e:
        row['outcome'] = 'reject'
        row['error_type'] = type(e).__name__
        row['error_msg'] = str(e)[:300]
    except Exception as e:  # anything else is an unhelpful vendor failure mode
        row['outcome'] = 'crash'
        row['error_type'] = type(e).__name__
        row['error_msg'] = str(e)[:300]
    finally:
        logging.getLogger().removeHandler(cap)
    if cap.records:
        row['warnings'] = sorted(set(cap.records))
    return row


def main():
    cfg_path = Path(sys.argv[1])
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / 'catalog'
    outdir.mkdir(exist_ok=True)
    cfg_name = cfg_path.stem

    logging.getLogger().setLevel(logging.WARNING)
    soccfg = QickConfig(str(cfg_path))
    meta = {'config': cfg_name,
            'cfg_sw_version': soccfg['sw_version'],
            'qick_version': qick.__version__,
            'numpy_version': np.__version__}

    ALL_AXES = ('length', 'freq', 'gain', 'phase', 'phrst', 'envelope',
                'mux', 'budget_wmem', 'budget_pmem', 'budget_regs',
                'readout', 'timing', 'channels', 'config')
    by_axis = {}
    for probe in build_probes(soccfg._cfg):
        row = run_probe(soccfg, probe)
        by_axis.setdefault(probe['axis'], []).append({**meta, **row})
    for axis in ALL_AXES:
        if axis not in by_axis:
            by_axis[axis] = [{**meta, 'axis': axis, 'outcome': 'skipped',
                              'note': 'no channel of the required type in this config'}]

    for axis, rows in sorted(by_axis.items()):
        path = outdir / f'{cfg_name}__{axis}.jsonl'
        with open(path, 'w') as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + '\n')
        counts = {}
        for r in rows:
            counts[r['outcome']] = counts.get(r['outcome'], 0) + 1
        print(f'{path.name}: {len(rows)} rows {counts}')


if __name__ == '__main__':
    main()
