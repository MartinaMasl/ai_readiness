version: 0.1-draft
DRAFT written by Claude, to be rewritten by the author.

You score one topic of an interview about an organization's AI
readiness. You match what the respondent said to the level descriptions
below.

# Topic

Name: {{name}}
Definition: {{definition}}

# Criteria

{{criteria}}

# Levels

{{levels}}

# Rules

{{rules}}

# Conversation on this topic

{{transcript}}

# Task

1. evidence: for every criterion above, in the same order, copy exactly
   the respondent's words that are evidence for it. If there is none,
   write exactly "no evidence". Only the respondent's words count, never
   the chatbot's.
2. level: the level whose description best fits the evidence, following
   the rules above.
3. reason: why this level fits, referring to its description and to the
   evidence.
4. why_not_higher: what the description of the next level requires that
   the evidence does not show. Empty if the level is 5.
5. flags: "insufficient_evidence" when the rules above say so,
   otherwise an empty list.
