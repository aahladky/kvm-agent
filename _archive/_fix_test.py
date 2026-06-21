"""Smoke-test the launch() vision-confirm fix on 2 reps via the real measure path."""
import os
os.chdir(r"C:\Dev\vllm")
from executive import Executive, Verifier
import measure
ex = Executive.open(executor_model="uitars-q4", verifier=Verifier())
try:
    measure.multiapp(ex, K=2, tag="fixtest")
finally:
    ex.close()
    print("FIXTEST DONE")
