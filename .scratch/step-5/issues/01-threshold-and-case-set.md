# Config threshold and case-set hash
Status: ready-for-agent · Blocked by: none

- `ReviewConfig.threshold` (default 0.0). Report marks the config's threshold row and prints
  the threshold the rule picks (highest t with recall within 5 points of t=0).
- `report --as-config v1_1` scores a results file as another config that differs only in
  name and threshold; refuses otherwise.
- Results files store `case_set`: a hash of the case ids, labels (without `reviewed`) and
  diffs. Report shows it and warns when it differs from the current cases.
- Config `v1_1` = v1 + threshold 0.7 (or what the rule picks).
Done when: `report <v1 file> --as-config v1_1` prints the v1_1 row; tests pass.
