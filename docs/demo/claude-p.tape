# Companion capture: headless Claude Code answering from the injected briefing.
# Produces claude-p.png (the GIF output is a vhs requirement; discard it).
Output claude-p-throwaway.gif

Set Shell zsh
Set FontSize 14
Set Width 1500
Set Height 1100
Set TypingSpeed 30ms
Set Padding 16
Set WaitPattern "❯"

Hide
Type "source demo-env.sh" Enter
Type "clear" Enter
Show

Type "# No daimon commands this time — just ask your agent:"
Enter
Sleep 1s

Type `claude -p "where did we leave off?"`
Enter
Wait@240s
Sleep 2s

Screenshot claude-p.png
Sleep 1s
