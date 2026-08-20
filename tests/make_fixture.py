#!/usr/bin/env python3
"""Build a small deterministic MCAP fixture (json encoding) for script tests.

Scenario (20s, t0=1000.0):
- /apa/perception (5 Hz): data.seq monotonic, obstacle distance; publisher goes
  SILENT for 2.0s at t=1008.0..1010.0 to create stale reuse downstream.
- /apa/decision (10 Hz): data.mirror_fold_dist starts ~0.60 with +-0.004 jitter,
  ramps below 0.15 at t=1012.0 and stays bad; single-frame glitch at t=1005.0
  (one sample at 0.10 then back) that debounce must reject.
  data.decDebugStr flips "state=Open" -> "state=Close" at t=1012.0.
"""
import json, sys
from mcap.writer import Writer

T0 = 1000.0

def jitter(i):  # deterministic pseudo-noise, no random module
    return ((i * 37) % 9 - 4) / 1000.0  # -0.004..0.004

def main(path, encoding="json"):
    with open(path, "wb") as fh:
        w = Writer(fh)
        w.start()
        schema = w.register_schema(name="apa.json", encoding="jsonschema", data=b"{}")
        ch_per = w.register_channel(topic="/apa/perception", message_encoding=encoding, schema_id=schema)
        ch_dec = w.register_channel(topic="/apa/decision", message_encoding=encoding, schema_id=schema)

        # perception 5 Hz with a 2s gap at 1008-1010
        seq = 0
        t = T0
        while t <= T0 + 20.0:
            if not (1008.0 <= t - 0.0 and t < 1010.0 + 0.0) or not (T0 + 8.0 <= t < T0 + 10.0):
                pass
            if not (T0 + 8.0 <= t < T0 + 10.0):
                msg = {"seq": seq, "header": {"timestamp": round(t - 0.01, 3)},
                       "obstacle_dist": round(1.5 - 0.02 * seq, 3)}
                ns = int(t * 1e9)
                w.add_message(channel_id=ch_per, log_time=ns, publish_time=ns - 2_000_000, sequence=seq,
                              data=json.dumps(msg).encode())
            seq += 1
            t = round(t + 0.2, 3)

        # decision 10 Hz
        for i in range(201):
            t = round(T0 + i * 0.1, 3)
            if t == T0 + 5.0:
                dist = 0.10          # single-frame glitch (sensor spike) -> debounce must reject
            elif t < T0 + 12.0:
                dist = round(0.60 + jitter(i), 4)   # normal float jitter
            else:
                dist = round(0.12 + jitter(i) / 10, 4)  # sustained bad: < 0.15
            state = "Open" if t < T0 + 12.0 else "Close"
            msg = {"seq": i, "header": {"timestamp": round(t - 0.005, 3)},
                   "mirror_fold_dist": dist, "decDebugStr": f"state={state};dist={dist}"}
            ns = int(t * 1e9)
            w.add_message(channel_id=ch_dec, log_time=ns, publish_time=ns - 1_000_000, sequence=i,
                          data=json.dumps(msg).encode())
        w.finish()
    print(f"fixture written: {path}")

def main_jitter(path, encoding="json"):
    """CASE-0001 style fixture: threshold flapping + slow trajectory-count gate."""
    with open(path, "wb") as fh:
        w = Writer(fh); w.start()
        sch = w.register_schema(name="apa.json", encoding="jsonschema", data=b"{}")
        dec = w.register_channel(topic="/apa/decision", message_encoding=encoding, schema_id=sch)
        plan = w.register_channel(topic="/apa/planning_output", message_encoding=encoding, schema_id=sch)
        for i in range(800):  # 20Hz, 40s; mirrorFoldDist flaps around 1.8 until t=30
            t = T0 + i * 0.05
            dist = (1.85 if (i // 3) % 2 == 0 else 1.75) if t <= T0 + 30 else 0.9
            state = 2 if t < T0 + 36.2 else 1
            ns = int(t * 1e9)
            w.add_message(channel_id=dec, log_time=ns, publish_time=ns, sequence=i,
                          data=json.dumps({"decStatus": {"rearMirrorState": state,
                                                          "mirrorFoldDist": round(dist, 3)},
                                           "header": {"timestamp": t}}).encode())
        for i, (ts, n) in enumerate([(0, 3), (16, 2), (30, 1), (39, 1)]):
            ns = int((T0 + ts) * 1e9)
            w.add_message(channel_id=plan, log_time=ns, publish_time=ns, sequence=i,
                          data=json.dumps({"header": {"timestamp": T0 + ts},
                                           "trajectoryNum": n}).encode())
        w.finish()


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[2] == "jitter":
        main_jitter(sys.argv[1])
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else "fixture.mcap",
             sys.argv[2] if len(sys.argv) > 2 else "json")
