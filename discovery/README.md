This directory contains files used to discover and analyze Ruida protocol characteristics or to serve as evidence for problems discovered in apps which use the Ruida protocol. This is essentially a growing list of test cases and their results. Each  test case or problem is contained in a sub-directory which in turn contains app project files to be run to capture interaction between the app and a Ruida controller. The specifics of the test case should be described in a README contained in the test case directory. The interactions are captured using the `capture` script. Once captured, the resulting log files are processed using the `decode` script. Two variations of the decoded data are simple and verbose.

Whenever possible a project file should be created for each app for comparison. Currently these apps are:
- RDWorks - Free and commonly included with Ruida controllers.
- MeerK40t - Open source laser control software.
- LightBurn - Popular and powerful paid licence.
# Naming Conventions
Test case directories should have names in the following format:
`tc_<id>`
The `<id>` can be a simple sequence number or a more descriptive name. Sequence numbers are recommended.
App project files should have the same name with the app ID and app file extension appended:
`tc_<id>-<app_id>.<ext>`

| App       | App ID | Extension |
| --------- | ------ | --------- |
| RDWorks   | rdw    | .rdw      |
| LightBurn | lb     | .lbrn2    |
| MeerK40t  | mk     | .svg      |
Capture log files and their decoded counterparts have the same names with the extension replaced with the log or decoded extensions. These are:
`.log` A capture log file.
`.txt` The decoded capture simple form.
`-vrb.txt` The decoded capture verbose form.

## Problem Discovery
There are times when apps produce files which cause unexpected Ruida controller behavior. Files needed to reproduce such problems use the prefix `prb-<id>` instead of the test case prefix. It is possible additional project files are needed to fully characterize a problem. Additional files should be named using the `prb-<id>-<subid>` form and placed in the `prb-<id>` directory along with the original discovery files.

The recommended format for a problem ID is the date of discovery having the form:
`yyyy-mm-dd-n`
Where:
- `yyyy` The year
- `mm` The month
- `dd` The day of the month
- `n` The sequence number of the problem discovered on that day.
# Test Case Index
This section is a directory of test cases. The test case directories are contained in the `tc` directory.

| Purpose<br>           | Spec                                  |
| --------------------- | ------------------------------------- |
| Jogging verification. | [tc-2025-10-09-1](tc-2025-10-09-1.md) |
| Simple rectangle      | [tc-2025-10-11-1](tc-2025-10-11-1.md) |

## Problem Index
This section is a directory of known problems. The problem directories are contained in the `prb` directory.

| Summary<br> | ID<br>                 |
| ----------- | ---------------------- |
|             | `prb-yyyy-mm-dd-n`<br> |

