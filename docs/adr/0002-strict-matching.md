# Strict matching: duplicates and wrong categories are false alarms

A finding is a hit only when category, file and side match a labelled issue and the line is within the issue's range plus tolerance. Findings are matched highest confidence first, and each claims one issue. A second finding on an already-claimed issue counts as a false alarm, and so does a finding at the right place with the wrong category. We chose this because the product promise is low noise: developers experience duplicates and miscategorised comments as noise, so the numbers must count them. A looser "found the spot, any category" recall is reported next to it as a diagnostic, so misses can be traced to detection or to classification.

**Considered options:** ignoring duplicates, and half credit for the right place with the wrong category. Both rejected because they make the reviewer look quieter and more accurate than it is to the person reading its comments.
