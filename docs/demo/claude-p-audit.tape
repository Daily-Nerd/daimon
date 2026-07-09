# Companion capture 2: the provenance self-audit — agent explains WHICH
# memories are verified quotes vs its own inferences, beliefs, and next steps.
# Taller canvas + smaller font so the long answer fits one frame.
Output claude-p-audit-throwaway.gif

Set Shell zsh
Set FontSize 12
Set Width 1500
Set Height 1720
Set TypingSpeed 25ms
Set Padding 16
Set WaitPattern "❯"

Hide
Type "source demo-env.sh" Enter
Type "clear" Enter
Show

Type `claude -p "where did we leave off? and tell me how you know that — which of your memories of this project are verified quotes vs your own inferences, what beliefs are you carrying, and what should I do next?"`
Enter
Wait@300s
Sleep 2s

Screenshot claude-p-audit.png
Sleep 1s
