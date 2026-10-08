# v0.8.1: fixes from the v0.8 live report

The package still exposes **97 tools**. On 2026-10-08, v0.8 live testing exercised
54 distinct names, including all 30 names added since v0.5. This patch addresses
its three reproduced failures and the diagnostic, journal, test and guidance gaps.

## Recovery

Native JSON import can create duplicate fabric names and change indices. Recovery
now maps unique pattern names to their **actual imported fabric assignments**.
Original groups must map to distinct, unsplit destinations; ambiguous split or
collapsed groups stop recovery before presets are overwritten. Failure reports
retain the stage, completed settings, checkpoint and recovery files.

Version 2 preset manifests record original indices, hashes and pattern associations.
Duplicate fabric names are supported when associations are unambiguous. Legacy
or unused fabrics can use `fabric_mapping={"0":1,"2":3}`: original index strings
to current global indices, covering exactly the backed-up records without collapse.
After `ReplaceFabric`, recovery calls the installed `SetFabricNameW(int,str)` and
checks the saved name and unchanged assignments. Required preset signatures are
checked before a JSON import changes the scene. Preset hashes/read-back do not
certify physics/cache/colorway equivalence. `list_fabrics` now inventories global
indices using `GetFabricCount(False)` and reports used assignments separately.

## Redrape and placement

Nonzero redrape translation had no effect in the live test. It now fails before
checkpoint/native calls with `code=unsupported_translation`,
`partial_change_possible=false` and `checkpoint_created=false`.
Whole-garment redrape without offsets remains available and can reset drape.
Use `arrange_patterns_by_name` or `arrange_patterns_verified` for supported avatar
placement with mesh evidence. No guessed rigid 3D setter was introduced.

## Fit evidence and detail

Intersection reports distinguish **proper_crossing**, **coplanar_overlap** and
**contact**, retaining OBJ object/group/material provenance, positions, bounds,
hashes and source-pair counts. Optional `body_regions` are caller-supplied
`{name,min:[x,y,z],max:[x,y,z]}` bounds, not inferred anatomy.
Ordinary mesh-adjacent contact is suppressed. Explicit `known_seam_triangle_pairs`
refer to this exact export and suppress only contact; crossings and coplanar
area overlaps remain visible. `review_output_dir` creates up to 25 SVG pair
closeups in XY/XZ/ZY projections, verifying unchanged mesh hashes. These are
geometric evidence, not native screenshots or physical collision sensors.

Fit reports separate `review_status`, `diagnostics_complete`, `review_findings`,
`review_incomplete` and `review_limits` from operation `ok`. Incomplete scans
cannot claim a clean result. Sparse clearance/open avatars retain review limits.
`fit_certified` remains false.

`preview_garment(capture_mode="custom_views",require_native_size=true)` captures
views already saved/zoomed in MD. Fit reports accept `preview_capture_mode`,
`preview_width`, `preview_height` and `require_native_size`. MCP dimensions are
limited to 2048. Strict capture rejects undersized PNGs before resampling.
Metadata records native render/request/output dimensions and resampling. Crops
record source/crop/output sizes and `detail_added=false`. No automatic camera
zoom API is claimed; configure useful saved views inside MD first.

## Journals, tests and runtime health

Malformed/torn journals retain parsed evidence, normalize unfinished starts to
uncertain, require inspection and consistently report `automatic_replay=false`.
`complete_operations` is a legacy parsed-record alias, not completed execution.
Tests now execute the actual generated runtime/cache dispatcher, legacy fallback,
cache loss, isolated globals and uncertain I/O. New regressions cover observed
fabric failures, partial recovery, contact classification, native capture sizes
and journal recovery. Run the suite and schema catalog check from the README.
The complete v0.8.1 regression suite passed **173/173** tests, including 43
additional regression cases. The README catalog matches all 97 live MCP schemas.

Status separates message dispatch health from host-specific release evidence.
The user confirmed normal idle orbiting/menus on Windows/MD 2026.0.315, and 13
real protocol checks passed. Native calls still run synchronously.
`listener_status` reports server/listener versions and `listener_restart_required`.
Reconnect MCP clients to install updated SHA-keyed operation source; stop and
click the registered launcher again to load updated listener code.

## Targeted live verification and remaining native limits

Patched JSON import and preset restore passed inside MD 2026.0.315 on the saved
dart/sleeve reproduction. Duplicate `FABRIC 1` names resolved to destination
index 1; 15 mm resolution, layer 1, solidify=true, assignments and the existing
dart seam survived. Independent restore preserved `FABRIC 1` instead of
`fabric_000`. Translation was safely rejected. The starting empty scene was
restored and its listener left running. Local task evidence is in
`outputs/md-v081-fixes`; garment assets are not bundled release fixtures.

Rigid per-piece transforms, native closure creation/grading and pressure/stress
sensing remain unsupported. Full sewn/draped sleeves, layered lining/closures,
GPU comparison, animation/export checks, body-specific grading and complete
physics/colorway/cache equivalence need dedicated live garment fixtures.
Zipper mutation has contract coverage; the live scene lacked a zipper style.
These remain explicitly unvalidated production workflows.

A later final connection probe was refused and process inspection found MD no
longer running; the cause of its exit was not established. Recovery evidence
above was captured before that exit. Start MD and the registered plugin again
to load the installed v0.8.1 listener.
