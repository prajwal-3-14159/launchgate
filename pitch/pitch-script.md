# LaunchGate: spoken pitch

About 3 minutes 30 seconds at a normal pace (roughly 480 words). To cut to 2 minutes, skip the parts marked *(optional)*. Slide numbers refer to `pitch.html` / `LaunchGate-pitch.pdf`.

---

## 1. Opening (slide 1, title) — 20 s

Hi, we're LaunchGate.

Your code is ready to ship. The question nobody on the team can answer quickly is: *is it allowed to ship in Japan? In the EU?*

LaunchGate answers that in about a minute.

## 2. The problem (slide 2) — 45 s

At a large company, clearing a release for a new market means security, privacy, legal and trade teams each doing their own review. It can run for months, and the findings live in four different places.

At a start-up, or if you're a solo builder shipping fast with AI-written code, you don't have those teams at all. You find out about the problem after you launch.

Code scanners help with part of this. They find secrets and vulnerable dependencies. But they stop at the code. They don't know what each market currently requires, and they can't tell you which rule a line of code runs into.

## 3. What it does (slide 2, the matrix) — 30 s

You give LaunchGate a repo and your target markets. It gives you a matrix: markets across the top, security, privacy, licences and trade down the side. Every cell says how many blockers and warnings.

And every finding points at two things: a specific line of code, and a dated web source you can open. If it can't show both, the finding is dropped.

## 4. How it works (slide 3) — 45 s

Five partners, each doing a job the others can't.

Daytona clones the repo into a sandbox and runs four open-source scanners, so untrusted code never touches our machine.

An open model running on Nosana reads the result and writes a fact sheet: what personal data the code handles, where it sends it.

Tavily searches for the current rules in each market, live.

On AgentField, one agent per market and domain matches the facts against the rules, in parallel.

*(optional)* Then comes the part we care about most. The model only proposes. Code decides. A finding survives only if its source is one we actually fetched and its line actually exists in the repo.

## 5. The result (slide 4) — 45 s

Here is a real run on our demo app, a small trip planner.

In 53 seconds it found nine blockers and six warnings across Japan and the EU: a hard-coded API key, a vulnerable dependency, user emails going to a US analytics vendor that the privacy policy never mentions, location stored with no consent step, and an AGPL library inside a closed-source product.

Then we pressed one button. CodeAF, running on the same Nosana model, wrote a patch for the hard-coded key in 32 seconds. Daytona applied it in the sandbox, re-ran the scanner, and ran the repo's own tests. Two out of two passed. Only then is the patch marked verified.

## 6. Trust and close (slide 5) — 30 s

A tool like this is only useful if you can check it. So it follows a few rules: every finding is cited, repo code only runs in a sandbox, a patch counts only when your tests pass, and nothing is pushed for you.

*(optional)* It also never tells you a product is cleared. It is triage before counsel, not a replacement for it.

For a large company, that is one shared first pass instead of months of back-and-forth. For a small team, it is the review they couldn't otherwise afford.

That's LaunchGate. The code is on GitHub. Thank you.

---

## If you get questions

- **"Is this legal advice?"** No. It is a first pass that tells counsel where to look, with sources. It never says a product is cleared.
- **"How do you stop the model making things up?"** Scanner findings involve no model at all. Model findings are filtered in code: no fetched source and real line, no finding.
- **"Does it work on any repo?"** Today: public Python repos with a `requirements.txt`. Japan and the EU are tested. On a larger repo (our own) the check took a few minutes, not one.
- **"Are results the same every run?"** Scanner findings are. Model-matched findings vary slightly between runs.
- **"Where does 'months' come from?"** That is our experience of how multi-team launch reviews go, not a measured figure.
