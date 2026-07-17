"""Task battery: the custom-tasks-first slice of PROJECT_GUIDANCE_holo.md §3.1-3.2.

Deliberately NOT importing WindowsAgentArena/OSWorld yet (that's the "full import" option
the guidance doc also lists) -- this is the small, fast-to-run set covering the five
coverage categories the guidance doc calls out as unverified by the existing Notepad/
Calculator runs: scroll/drag_and_drop, long-horizon (stresses MAX_HISTORY_IMAGES=3
eviction), wait-type, deliberately-impossible, and small/dense targets. Two "core" tasks
are included as a cheap regression floor -- they mirror already-verified-working goals
from FINDINGS_integration.md's Phase I5 re-test, so a battery run always has a known-good
signal to compare novel-task failures against.

Every task is graded by an independent screen check (Verifier: OCR or vision Q&A against
the FINAL frame), never by the model's own self-report alone -- self-reported success was
exactly the failure mode Phase I5 found and the vendor-alignment pass fixed (see
FINDINGS_integration.md's "Latency investigation" section and the whole vendor-alignment
table). `expect_answer=False` tasks invert the check: correct == the model did NOT
falsely call `answer` on an impossible request.

These are goal STRINGS run against whatever the VM's current desktop state is -- unlike
the old EvoCUA-era harness, there is no automated reset_clean step here yet. Run tasks
that mutate persistent state (drag_file, long_horizon_notes) with that in mind, or clean
up the desktop between battery runs by hand.
"""
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Task:
    id: str
    category: str        # core | scroll_drag | long_horizon | wait | impossible | small_target
    goal: str             # instruction passed straight to agent_loop_holo.run()
    max_steps: int = 10
    grade: Optional[Callable[[bytes, object], "bool | None"]] = None
    expect_answer: bool = True   # False for tasks where honest refusal IS success
    notes: str = ""


def _text_grader(expect: str):
    """OCR/text-transcription substring check against the final frame."""
    def g(png, verifier):
        return verifier.has_text(png, expect)
    return g


def _vision_grader(question: str):
    """Vision yes/no about the final frame. Mirrors Executive.confirm()'s convention of
    calling Verifier._vision directly (the established extension point in this codebase --
    see tests/test_closed_loop.py's FakeVerifier)."""
    def g(png, verifier):
        ans = verifier._vision(png, question + " Answer only 'yes' or 'no'.")
        return None if ans is None else ("yes" in ans.lower())
    return g


TASKS = [
    # -- core: cheap regression floor, mirrors known-good FINDINGS_integration.md runs --
    Task(
        id="calc_basic", category="core", max_steps=8,
        goal="Open Calculator, compute 7 x 8, and confirm the result.",
        grade=_text_grader("56"),
        notes="Mirrors the post-alignment re-test in FINDINGS_integration.md (7 steps, called answer).",
    ),
    Task(
        id="notepad_type", category="core", max_steps=8,
        goal="Open Notepad and type 'holo battery test'.",
        grade=_vision_grader("Does the Notepad window contain the text 'holo battery test'?"),
        notes="Mirrors Phase I5's Notepad launch+type goal.",
    ),

    # -- scroll / drag_and_drop: vendor-unverified extensions (holo.py's module docstring) --
    Task(
        id="scroll_to_about", category="scroll_drag", max_steps=14,
        goal="Open Settings, scroll down the page, and click 'About'.",
        grade=_vision_grader("Is a page titled 'About' showing device/system specs now visible?"),
    ),
    Task(
        id="drag_file_to_desktop", category="scroll_drag", max_steps=16,
        goal=("Open File Explorer, navigate to Documents, drag any file onto the Desktop "
              "entry in the left sidebar, then confirm it copied."),
        grade=_vision_grader("Is a file now visible on the Windows Desktop?"),
        notes="Needs at least one file present in Documents beforehand.",
    ),

    # -- long-horizon: stresses MAX_HISTORY_IMAGES=3 eviction (does it lose an early fact?) --
    Task(
        id="long_horizon_recall", category="long_horizon", max_steps=20,
        goal=("Open Notepad and type 'code: 4471'. Then open Calculator and compute "
              "4471 + 1000. Then switch back to Notepad and type the calculator's result "
              "on a new line."),
        grade=_vision_grader("Does the Notepad window show both '4471' and '5471'?"),
        notes=("The vendor docs flag screenshot eviction as a cause of forgetting earlier "
               "facts -- this task requires recalling '4471' after several later screenshots."),
    ),

    # -- wait-type: does it poll sensibly on a slow-loading app, or flail? --
    Task(
        id="wait_settings_system", category="wait", max_steps=12,
        goal="Open the Windows Settings app, wait for it to fully load, then click 'System'.",
        grade=_vision_grader("Is a System settings page now visible?"),
    ),

    # -- deliberately impossible: the pre-alignment failure mode was never signaling done;
    #    the happy path is fixed now, this is the unhappy path --
    Task(
        id="impossible_app", category="impossible", max_steps=8, expect_answer=False,
        goal="Open the application 'Photoshop' and create a new document.",
        grade=None,
        notes=("Photoshop is not installed on this VM. Correct behavior: the model gives "
               "up / reports it cannot find the app, and does NOT call `answer` as if it "
               "succeeded (that false-positive-finish was Phase I5's original finding)."),
    ),

    # -- small / dense targets: grounding-accuracy floor, also the 3.4 dual-resolution baseline --
    Task(
        id="small_target_tray", category="small_target", max_steps=6,
        goal="Click the small network/WiFi icon in the system tray at the bottom-right of the taskbar.",
        grade=_vision_grader("Did a network flyout panel open on screen?"),
    ),
]
