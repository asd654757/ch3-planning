"""Predeclared zero-API feasibility of postcontact retry evidence."""
import argparse
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("MUJOCO_GL", "egl")

from ch3.repair.budgeted_recovery_fixture import RecoveryFixture
from ch3.execution.recovery_contact_evidence import aperture_evidence, regrasp_gate


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output",type=Path,required=True)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    sources=[Path(__file__),Path("ch3/execution/recovery_contact_evidence.py")]
    (args.output/"manifest.json").write_text(json.dumps({"seeds":[1000,1001,1002],
        "cases":["normal","released","precontact"],"diagnostic_close_steps":30,
        "geometry_not_fitted_to_results":True,"sensor_dropout_disabled_for_all_cases":True,
        "source_hashes":{str(s):hashlib.sha256(s.read_bytes()).hexdigest() for s in sources}},indent=2))
    rows=[]
    for seed in (1000,1001,1002):
        for case in ("normal","released","precontact"):
            fixture=None
            row={"seed":seed,"case":case,"status":"error"}
            try:
                fixture=RecoveryFixture(seed,case,args.output/f"seed_{seed}_{case}")
                fixture.blackout=0
                fixture.phase="diagnostic_close"
                fixture.grip=1.
                samples=[]
                for i in range(30):
                    fixture.env.step(fixture.np.array([0.,0.,0.,1.]))
                    samples.append({"commanded_gripper":1,
                        "jaw_body_distance_m":float(fixture.np.linalg.norm(
                            fixture.env.data.body("rightclaw").xpos-fixture.env.data.body("leftclaw").xpos)),
                        "hand_z_m":float(fixture.env.get_endeff_pos()[2])})
                aperture=aperture_evidence(samples[-5:])
                fixture.perform("observe",1000)
                from ch3.execution.visual_holding import failed_pick_evidence
                obs=fixture.latest
                negative=failed_pick_evidence(fixture.initial_blue,
                    [s["target_pixel"] for s in obs["samples"]],[s["hand_pixel"] for s in obs["samples"]])
                gate=regrasp_gate(aperture,negative,target_visible=obs["samples"][-1]["target_pixel"] is not None,
                                  observation_id=obs["id"])
                # Ground truth appears only AFTER all gate computations.
                row.update(status="complete",samples=samples,aperture=aperture,
                    holding=obs["holding"],nonfollowing=negative,regrasp_gate=gate,
                    episode_resets=int(fixture.env._ch3_reset_count),
                    evaluation_only={"blue_height_m":float(fixture.env.data.body("candidate_blue").xpos[2])},
                    explicit_steps=fixture.steps,api_calls=0)
            except Exception as exc:
                row["error"]={"type":type(exc).__name__,"message":str(exc)}
            finally:
                if fixture:
                    fixture.close()
            rows.append(row)
            (args.output/"results.json").write_text(json.dumps(rows,indent=2))
            print(json.dumps({k:row.get(k) for k in ("seed","case","status","aperture","regrasp_gate","error")}),flush=True)
    if any(r["status"]!="complete" for r in rows):
        raise SystemExit(1)


if __name__=="__main__":
    main()
