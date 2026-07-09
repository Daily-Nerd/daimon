# Recording script for the README demo GIF (see README.md in this directory).
# Prereqs: both transcripts serialized into DAIMON_CHECKPOINT_DIR, `daimon` on
# PATH, cwd = the demo project directory. Update the two ids in the resolve
# command to match your run (read them off the ⚠ line in the first brief).
Output daimon-demo.gif

Set Shell zsh
Set FontSize 14
Set Width 1500
Set Height 1000
Set TypingSpeed 35ms
Set Padding 16

Type "# Session 1: studied for the AWS cert. Session 2: pivoted to GCP."
Enter
Sleep 1s
Type "# New session — what does daimon remember, and does it tell the truth?"
Enter
Sleep 1.5s

Type "daimon brief"
Enter
Sleep 9s

Screenshot daimon-demo-flag.png

Type "# It flagged the stale AWS decision instead of feeding it back as fact."
Enter
Sleep 1.5s
Type "# One command to confirm the supersession — you stay in charge:"
Enter
Sleep 1s

Type "daimon resolve r-738141 --status superseded-by:r-dd42b4"
Enter
Sleep 3.5s

Type "daimon brief"
Enter
Sleep 7s

Type "# Stale decision withheld. Every memory tagged verbatim or inferred, with its quote."
Enter
Sleep 3s
