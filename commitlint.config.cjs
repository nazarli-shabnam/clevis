module.exports = {
  extends: ["@commitlint/config-conventional"],
  ignores: [
    (message) => message.startsWith("Merge pull request "),
    (message) => message.startsWith("Merge branch "),
    (message) => message.startsWith("Merge remote-tracking branch "),
    // Local merges of a bare branch name ("Merge feat/x"), as `git merge <branch>` writes them.
    (message) => /^Merge [^\s:]+\s*$/.test(message.split("\n", 1)[0]),
  ],
}

