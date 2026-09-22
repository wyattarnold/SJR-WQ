# Gap analysis
Where the observational record runs out, measured from `data/processed`. Regenerate with `python -m sjrwq.gaps` after a harvest.
Inventory as built: **1,834 source records** collapsing to **1,350 unique stations**, 283 of them appearing in more than one source.

Two conventions apply throughout. A station counts as observing the river only where it sits on a natural channel or serves as a permitted receiving-water point. The inventory still lists and maps canals, agricultural drains, and monitoring wells, and none of the three closes a river gap however close to the centerline it falls. The coverage tables run over 2011-2025, the window in `config/domain.yml`, because a gage that ran from 1955 to 1970 is history rather than coverage, and a record has to reach the second half of the window, which begins July 2, 2018, to count. The mainstem section gives the all-time station count beside the windowed one, and the boundary and reservoir sections name each match that changes without that rule.

## Mainstem continuous coverage
Gaps of 15 km or more along the San Joaquin between Vernalis (0 km) and the headwaters. Only continuous records count, because quarterly samples cannot calibrate a temperature model, and only stations on a natural channel or a permitted receiving-water point count, because a monitoring well two hundred meters from the bank does not observe the river. A station also needs a record reaching the second half of 2011-2025; see `coverage.reporting_since`.

**water temperature.** Along 475.3 km of mainstem, 22 stations on the river hold a continuous record reaching the second half of 2011-2025, which begins July 2, 2018. 28 have held one at some point. Counting every channel class over the same window gives 31, and the 9 extra are 7 groundwater, 1 conveyance, and 1 effluent stations. 8 of those 9 fall inside 4 of the gaps listed below and split each one, and the longest gap stays 102.7 km.

| from (km) | to (km) | length (km) |
| ---: | ---: | ---: |
| 351.1 | 453.8 | 102.7 |
| 99.8 | 155.0 | 55.2 |
| 8.2 | 46.6 | 38.4 |
| 175.6 | 208.3 | 32.7 |
| 311.6 | 340.9 | 29.3 |
| 208.3 | 229.8 | 21.5 |
| 270.9 | 292.4 | 21.5 |
| 453.8 | 475.3 | 21.5 |
| 155.0 | 175.6 | 20.6 |
| 229.8 | 248.8 | 19.0 |
| 60.0 | 77.7 | 17.7 |
| 292.4 | 309.2 | 16.8 |

**specific conductance.** Along 475.3 km of mainstem, 16 stations on the river hold a continuous record reaching the second half of 2011-2025, which begins July 2, 2018. 19 have held one at some point. Counting every channel class over the same window gives 23, and the 7 extra are 6 groundwater and 1 conveyance stations. 7 of those 7 fall inside 4 of the gaps listed below and split each one, and the longest gap stays 163.7 km.

| from (km) | to (km) | length (km) |
| ---: | ---: | ---: |
| 311.6 | 475.3 | 163.7 |
| 99.8 | 155.0 | 55.2 |
| 8.2 | 60.0 | 51.8 |
| 175.6 | 208.3 | 32.7 |
| 208.3 | 229.8 | 21.5 |
| 270.9 | 292.4 | 21.5 |
| 155.0 | 175.6 | 20.6 |
| 229.8 | 249.2 | 19.4 |
| 60.0 | 77.7 | 17.7 |
| 292.4 | 309.2 | 16.8 |

**discharge.** Along 475.3 km of mainstem, 30 stations on the river hold a continuous record reaching the second half of 2011-2025, which begins July 2, 2018. 40 have held one at some point. Counting every channel class over the same window gives 43, and the 13 extra are 10 conveyance, 2 reservoir, and 1 effluent stations. 9 of those 13 fall inside 4 of the gaps listed below and split each one, and counting them moves the longest gap from 55.2 km to 54.5 km.

| from (km) | to (km) | length (km) |
| ---: | ---: | ---: |
| 398.6 | 453.8 | 55.2 |
| 100.5 | 155.0 | 54.5 |
| 311.6 | 351.1 | 39.5 |
| 8.2 | 46.6 | 38.4 |
| 175.6 | 208.3 | 32.7 |
| 208.3 | 229.8 | 21.5 |
| 270.9 | 292.4 | 21.5 |
| 453.8 | 475.3 | 21.5 |
| 229.9 | 248.8 | 18.9 |
| 366.9 | 384.7 | 17.8 |
| 60.0 | 77.2 | 17.2 |
| 292.4 | 309.2 | 16.8 |
| 351.1 | 366.9 | 15.8 |

## Boundary and confluence coverage
Distance from each control point to the nearest continuous station measuring the named parameter. Beyond a few kilometers, the boundary condition needs interpolation or a request.

| control point | nearest continuous temperature | km | nearest continuous EC | km |
| --- | --- | ---: | --- | ---: |
| Merced River confluence | SAN JOAQUIN R AB MERCED R NR NEWMAN CA | 0.2 | SAN JOAQUIN R AB MERCED R NR NEWMAN CA | 0.2 |
| Stanislaus River confluence | SAN JOAQUIN R NR VERNALIS CA | 2.5 | SAN JOAQUIN R NR VERNALIS CA | 2.5 |
| Tuolumne River confluence | INGRAM CREEK | 4.6 | INGRAM CREEK | 4.6 |
| Friant Dam (upstream boundary) | SAN JOAQUIN R RELEASE A FRIANT DAM CA | 0.1 | SAN JOAQUIN R RELEASE A FRIANT DAM CA | 0.1 |
| Mendota Pool | SAN JOAQUIN R NR MENDOTA CA | 3.8 | SAN JOAQUIN R NR MENDOTA CA | 3.8 |
| Salt Slough inflow | SALT SLOUGH A HWY 165 NR STEVINSON CA | 0.0 | SALT SLOUGH A HWY 165 NR STEVINSON CA | 0.0 |
| Mud Slough inflow | MUD SLOUGH NR GUSTINE CA | 0.0 | MUD SLOUGH NR GUSTINE CA | 0.0 |
| Vernalis (downstream boundary) | SAN JOAQUIN R NR VERNALIS CA | 0.0 | SAN JOAQUIN R NR VERNALIS CA | 0.0 |

Without the rule that a record reach the second half of 2011-2025, 1 of the 16 matches changes: Tuolumne River confluence takes `TUOLUMNE R AT TUOLUMNE CITY` for temperature, 3.8 km away, with a continuous temperature record ending January 2012.

## Reservoir release temperature
Release temperature sets the upstream boundary for every reach below a rim dam. Distance to the nearest continuous temperature station is a weak proxy for whether a station observes that release, and the eight harvested sources hold a flagged depth profile at 0 of the 6 dams. A reservoir release model needs the profiles.

Two filters shape this table. Only stations classed natural or receiving_water count, so the search skips the conveyance, drain, effluent, groundwater, reservoir, and unknown classes. Records that stopped are out, because a gage that went quiet looks like an observed boundary condition without being one. With both filters off, 3 of the 6 dams take a different station: New Melones (Stanislaus) takes `NEW MELONES POWERHOUSE TAILRACE` (conveyance), 0.6 km away, with a continuous temperature record ending November 2011; Don Pedro (Tuolumne) takes `TUOLUMNE R BLW LA GRANGE DAM (CAFG)` (natural), 4.1 km away, with a continuous temperature record ending December 2011; Lake McClure (Merced) takes `MERCED RIVER BELOW NEW EXCHEQUER DAM` (natural), 1.8 km away, with a continuous temperature record ending July 2011.

This table matches each dam against stations on its own river, or in its own subbasin where the river has no traced centerline. On distance alone, 1 of the 6 dams takes a different station: Lake McClure (Merced) takes `TUOLUMNE R BL LAGRANGE DAM NR LAGRANGE CA` on the Tuolumne River, 17.5 km away.

Read the distances as an upper bound on how well the boundary is known, not a lower one. On a traced river, the match sits below the dam for Millerton Lake (Friant), New Melones (Stanislaus), and Don Pedro (Tuolumne). The match sits above the dam for Lake McClure (Merced), so that station measures the inflow side of the dam rather than the release. Hensley Lake (Fresno R) and Eastman Lake (Chowchilla R) sit on rivers with no traced centerline, so this report does not place their matches above or below the dam.

| reservoir | nearest continuous station on the river, with a record reaching the second half of 2011-2025 (from July 2, 2018) | km |
| --- | --- | ---: |
| Millerton Lake (Friant) | SAN JOAQUIN R RELEASE A FRIANT DAM CA | 0.2 |
| New Melones (Stanislaus) | STANISLAUS R BL TULLOCH PP NR KNIGHTS FERRY CA | 10.5 |
| Don Pedro (Tuolumne) | TUOLUMNE R BL LAGRANGE DAM NR LAGRANGE CA | 4.3 |
| Lake McClure (Merced) | MERCED R NR BRICEBURG #2 | 26.6 |
| Hensley Lake (Fresno R) | FRESNO R BLW HIDDEN DAM | 3.1 |
| Eastman Lake (Chowchilla R) | CHOWCHILLA R BL BUCHANAN DAM NR RAYMOND CA | 1.5 |

RISE publishes series within 5 km of 2 of the 6 dams and flags a depth profile at 0 of those 2. Reclamation, the USACE Sacramento District, the Turlock and Modesto Irrigation Districts, and Merced Irrigation District hold the profiles.

## Return-flow and drain monitoring points
An inventory of what exists, not a list of what is missing. Municipal and industrial monitoring points and agricultural drains within 25 km of the San Joaquin or one of the mapped tributaries. By primary source record, the 120 rows in the table come from eSMR (50), the Water Quality Portal (34), CEDEN (32), USGS (3), and DWR's Water Data Library (1). By channel class and station type, 70 of the points are drains and 24 are effluent points, the discharges a water or salt balance takes as boundary terms. 3 have the station type `influent` and sample the water entering a plant; the `type` column lists them as effluent. 23 are receiving-water points, river samples taken under a discharge permit, and count as observing the river under the convention at the top of this report.

The shortlist in `catalog_filtered.csv` includes the points holding 3 years or more of temperature or conductance since 2000, because reporting frequency does not decide membership: a permit obliging monthly conductance, against a gaged flow, still gives a monthly salt load. The columns below hold the measured reporting frequency so the difference stays visible, in the vocabulary the harvest uses. `daily` on a flow column is an average daily discharge, a real load term; `irregular` is a series whose measured interval between samples exceeds 400 days; and `reported` marks a row with no stated frequency and no interval to measure, such as a single sample.

One caution on position. An effluent row holds the coordinate of the treatment plant, not of the pipe outlet, and the two can be kilometers apart, so Manteca appears 11.4 km from the river and still discharges to it.

A further 3 discharge points and drains fall inside the same 25 km, and `point_discharges` leaves them out of the table: 0 report no target parameter, and 3 report only chemistry outside flow, temperature, and EC. `catalog.csv` still lists them under a `channel_class` of drain, effluent, or receiving_water.

| monitoring point | type | reach | km | flow | temp | EC | samples | record |
| --- | --- | --- | ---: | :-: | :-: | :-: | ---: | --- |
| City of Modesto WQCF RSW-004 | receiving_water | San Joaquin | 0.0 | - | monthly | monthly | 119 | 2011-2017 |
| El Portal WWTF R-001 | receiving_water | Merced | 0.0 | weekly | monthly | monthly | 140 | 2013-2014 |
| El Portal WWTF RSW-002 | receiving_water | Merced | 0.0 | - | monthly | monthly | 427 | 2014-2026 |
| Grayson Road Drain at Grayson | drain | San Joaquin | 0.0 | - | quarterly | monthly | 69 | 2000-2010 |
| J.F. Enterprises Worm Farm EFF-001 | effluent | Stanislaus | 0.0 | monthly | - | quarterly | 166 | 2012-2024 |
| J.F. Enterprises Worm Farm INF-001 | effluent | Stanislaus | 0.0 | - | - | quarterly | 44 | 2012-2024 |
| J.F. Enterprises Worm Farm RSW-001 | receiving_water | Stanislaus | 0.0 | - | quarterly | quarterly | 56 | 2012-2024 |
| Merced River Fish Hatchery EFF-001 | effluent | Merced | 0.0 | weekly | monthly | monthly | 43 | 2010-2026 |
| Merced River Fish Hatchery EFF-002 | effluent | Merced | 0.0 | daily | - | - | 3 | 2012-2024 |
| Turlock City, Turlock Regional Water Quality Control Facility RSW-004 | receiving_water | San Joaquin | 0.0 | - | weekly | weekly | 462 | 2010-2014 |
| Blewitt MWC Drain at Hwy 132 | drain | San Joaquin | 0.1 | - | monthly | - | 37 | 2004-2025 |
| HARDING DRAIN A CARPENTER RD NR PATTERSON CA | drain | San Joaquin | 0.1 | daily | daily | daily | - | 1992-1994 |
| Turlock City, Turlock Regional Water Quality Control Facility EFF-001 | effluent | San Joaquin | 0.1 | daily | daily | weekly | 6,062 | 2010-2026 |
| Turlock City, Turlock Regional Water Quality Control Facility EFF-002 | effluent | San Joaquin | 0.1 | daily | daily | weekly | 466 | 2013-2015 |
| Turlock City, Turlock Regional Water Quality Control Facility RSW-003 | receiving_water | San Joaquin | 0.1 | weekly | weekly | weekly | 783 | 2010-2015 |
| El Portal WWTF EFF-001 | effluent | Merced | 0.2 | daily | daily | weekly | 9,386 | 2014-2026 |
| El Portal WWTF M-001 | effluent | Merced | 0.2 | daily | daily | weekly | 1,006 | 2013-2014 |
| Jacobson Drain | drain | Tuolumne | 0.2 | - | quarterly | - | 17 | 2004-2008 |
| TID 5 Harding Drain @ Carpenter Road | drain | San Joaquin | 0.3 | - | quarterly | monthly | 126 | 2000-2016 |
| El Portal WWTF R-002 | receiving_water | Merced | 0.4 | weekly | monthly | monthly | 142 | 2013-2014 |
| El Portal WWTF RSW-001 | receiving_water | Merced | 0.4 | daily | monthly | monthly | 4,504 | 2014-2026 |
| Juncture of Poso Drain and Pick Anderson Bypass | drain | San Joaquin | 0.4 | - | reported | - | 1 | 2004-2004 |
| El Portal WWTF M-INF | effluent | Merced | 0.6 | - | - | weekly | 87 | 2013-2014 |
| Unnamed Drain @ Hogin Rd | drain | San Joaquin | 0.6 | - | quarterly | - | 55 | 2013-2023 |
| August Road Drain upstream of Crows Landing Bridge (Hogin Rd) | drain | San Joaquin | 0.7 | - | monthly | - | 3 | 2004-2004 |
| Jones Drain @ Oakdale Rd | drain | Merced | 0.8 | - | monthly | monthly | 46 | 2005-2007 |
| San Joaquin Fish Hatchery and Salmon Conservation and Research Facility EFF-001 | effluent | San Joaquin | 0.8 | weekly | monthly | monthly | 954 | 2009-2026 |
| Marshall Road Drain near River Road | drain | San Joaquin | 1.1 | - | quarterly | - | 155 | 2004-2025 |
| Unnamed Drain @ Pomelo Avenue near Paradise Avenue | drain | San Joaquin | 1.2 | - | monthly | - | 9 | 2003-2003 |
| Clovis WWTF RSW-002D | receiving_water | San Joaquin | 1.3 | monthly | quarterly | monthly | 102 | 2017-2024 |
| Island Field Drain at Catrina Rd | drain | San Joaquin | 1.3 | - | quarterly | - | 6 | 2004-2005 |
| Main Drain Spill | drain | Stanislaus | 1.4 | - | quarterly | - | 21 | 2004-2009 |
| Prairie Flower Drain @ Crows Landing Rd | drain | San Joaquin | 1.4 | - | monthly | - | 210 | 2005-2025 |
| J.F. Enterprises Worm Farm RSW-002 | receiving_water | San Joaquin | 1.5 | - | quarterly | quarterly | 56 | 2012-2024 |
| MID Main Drain Inlet to Miller Lake | drain | Stanislaus | 1.5 | - | weekly | - | 2 | 2003-2003 |
| Silva Drain @ Meadow Dr | drain | Merced | 1.6 | - | monthly | - | 34 | 2006-2008 |
| Levee Drain @ Carpenter Rd | drain | San Joaquin | 1.7 | - | quarterly | - | 82 | 2012-2025 |
| Poso Drain at NE corner of Turner Island and Palazzo Rd | drain | San Joaquin | 1.7 | - | monthly | - | 5 | 2004-2004 |
| Unnamed Drain @ Hwy 140 | drain | San Joaquin | 1.8 | - | annual | - | 20 | 2013-2022 |
| Hilmar Drain @ Central Ave | drain | San Joaquin | 2.1 | - | quarterly | - | 124 | 2005-2025 |
| Hilmar Drain @ Mitchell Rd | drain | Merced | 2.4 | - | weekly | - | 2 | 2008-2008 |
| MID Main Drain Inlet @ Shoemake Rd | drain | Stanislaus | 2.4 | - | annual | - | 25 | 2003-2012 |
| Prairie Flower Drain at Morgan Road | drain | San Joaquin | 2.4 | - | monthly | - | 6 | 2008-2008 |
| Reclamation Drain @ Williams Ave | drain | Merced | 2.4 | - | reported | - | 1 | 2008-2008 |
| Main Drain at DeForest Ranch | drain | Stanislaus | 2.7 | - | quarterly | - | 4 | 2007-2008 |
| TILE DRAIN FBH8061 | drain | San Joaquin | 2.7 | - | quarterly | quarterly | 25 | 1998-2002 |
| TILE DRAIN FBH2016 | drain | San Joaquin | 3.1 | - | quarterly | quarterly | 32 | 1998-2002 |
| Westport Drain @ Jennings Rd | drain | San Joaquin | 3.1 | - | monthly | - | 10 | 2003-2003 |
| Stanislaus River Drain @ South Airport Way | drain | San Joaquin | 3.5 | - | monthly | - | 3 | 2008-2008 |
| San Luis Drain @ Terminus | drain | San Joaquin | 3.6 | - | weekly | daily | 1,430 | 2000-2013 |
| TILE DRAIN BVS8003 | drain | San Joaquin | 3.7 | - | quarterly | quarterly | 35 | 1998-2002 |
| Clovis WWTF R-002U | receiving_water | San Joaquin | 3.8 | - | reported | reported | 2 | 2009-2013 |
| Clovis WWTF RSW-002U | receiving_water | San Joaquin | 3.8 | monthly | annual | annual | 174 | 2014-2025 |
| West San Juan Drain #1 W of Carlucci Road | drain | San Joaquin | 3.9 | - | reported | - | 1 | 2007-2007 |
| Moccasin Creek Fish Hatchery EFF-001 | effluent | Tuolumne | 4.0 | daily | weekly | monthly | 1,341 | 2010-2026 |
| Moccasin Creek Fish Hatchery RSW-002 | receiving_water | Tuolumne | 4.0 | - | monthly | monthly | 259 | 2013-2026 |
| TILE DRAIN VNS3733 | drain | San Joaquin | 4.0 | - | quarterly | quarterly | 24 | 2012-2015 |
| Moccasin Creek Fish Hatchery EFF-002 | effluent | Tuolumne | 4.1 | monthly | quarterly | quarterly | 65 | 2010-2020 |
| Holland Drain @ Hudson | drain | San Joaquin | 4.2 | - | quarterly | - | 2 | 2005-2005 |
| Moccasin Creek Fish Hatchery RSW-001 | receiving_water | Tuolumne | 4.2 | - | monthly | monthly | 261 | 2013-2026 |
| Moccasin Creek Fish Hatchery INF-001 | effluent | Tuolumne | 4.3 | - | - | monthly | 195 | 2013-2026 |
| Mootz Drain downstream of Langworth Pond | drain | Stanislaus | 4.3 | - | quarterly | monthly | 268 | 2009-2024 |
| Mootz Drain @ Langworth Rd | drain | Stanislaus | 4.4 | - | monthly | monthly | 46 | 2008-2009 |
| New Jerusalem Tile Drain | drain | San Joaquin | 4.5 | - | quarterly | monthly | 94 | 2000-2011 |
| TILE DRAIN BVS7402 | drain | San Joaquin | 4.5 | - | quarterly | quarterly | 6 | 2002-2002 |
| TILE DRAIN FBH4045 | drain | San Joaquin | 4.6 | - | annual | reported | 3 | 2002-2002 |
| TILE DRAIN FBH5056 | drain | San Joaquin | 4.6 | - | quarterly | quarterly | 5 | 2002-2002 |
| Westport Drain @ Vivian Rd | drain | San Joaquin | 4.6 | - | quarterly | - | 80 | 2007-2025 |
| SAN LUIS DR SITE B NR STEVINSON CA | drain | San Joaquin | 4.7 | daily | daily | daily | - | 1998-2005 |
| San Luis Drain - Gun Club Road | drain | San Joaquin | 5.1 | - | weekly | - | 571 | 2013-2025 |
| Hatch Drain @ Tuolumne Rd | drain | San Joaquin | 5.3 | - | quarterly | - | 99 | 2007-2025 |
| STORM DRAIN INLET W SIDE WHITEHORSE AVE A MODESTO | drain | Stanislaus | 6.5 | - | reported | - | 1 | 2005-2005 |
| Former JR Simplot (Winton Facility) EFF-001 | effluent | Merced | 6.6 | daily | monthly | monthly | 3,166 | 2017-2026 |
| Boundary Drain at Henry Miller Ave | drain | San Joaquin | 6.9 | - | monthly | - | 6 | 2005-2005 |
| Livingston Drain | drain | San Joaquin | 6.9 | - | annual | - | 11 | 2004-2008 |
| Drain 11 at Oleander Avenue | drain | Stanislaus | 7.0 | - | quarterly | - | 4 | 2007-2007 |
| North Valley Regional Recycled Water Program - Modesto WWTP & Turlock RWQCF DMC-001 | effluent | San Joaquin | 7.1 | - | weekly | weekly | 915 | 2017-2026 |
| Turlock City, Turlock Regional Water Quality Control Facility RSW-002 | receiving_water | San Joaquin | 7.2 | - | weekly | monthly | 733 | 2010-2024 |
| TILE DRAIN BVS6016 | drain | San Joaquin | 7.3 | - | quarterly | quarterly | 29 | 1998-2002 |
| Turlock City, Turlock Regional Water Quality Control Facility RSW-001 | receiving_water | San Joaquin | 7.3 | weekly | weekly | monthly | 872 | 2010-2025 |
| Big Creek Powerhouse No 1 WWTF EFF-001 | effluent | San Joaquin | 7.6 | daily | daily | weekly | 3,133 | 2013-2016 |
| Sonora Regional WWTP EFF-001 | effluent | Stanislaus | 8.1 | daily | daily | monthly | 92 | 2011-2011 |
| Livingston Drain @ Robin Ave | drain | Merced | 8.2 | - | quarterly | - | 91 | 2007-2025 |
| North Valley Regional Recycled Water Program - Modesto WWTP & Turlock RWQCF DMC-002 | effluent | San Joaquin | 8.4 | - | weekly | weekly | 913 | 2017-2026 |
| Drain 11 @ Walsal Slough (Top of Bank) | drain | San Joaquin | 9.0 | - | quarterly | - | 16 | 2004-2008 |
| Deuel Vocational Institution EFF-003 | effluent | San Joaquin | 9.2 | daily | - | monthly | 802 | 2012-2014 |
| Balsam Meadows Hydro Project EFF-002 | effluent | San Joaquin | 9.6 | quarterly | - | - | 2 | 2025-2025 |
| Deuel Vocational Institution EFF-001 | effluent | San Joaquin | 9.6 | daily | weekly | monthly | 4,900 | 2012-2022 |
| Drain at Wicklund Road | drain | Tuolumne | 9.8 | - | - | monthly | 26 | 2009-2010 |
| Mattos Drain @ Range Rd | drain | San Joaquin | 11.2 | - | reported | - | 1 | 2006-2006 |
| City of Manteca WW Quality Control Facility EFF-001 | effluent | San Joaquin | 11.4 | daily | daily | monthly | 21,800 | 2010-2026 |
| Drain at Paradise Cut and Hwy 205 | drain | San Joaquin | 11.6 | - | - | quarterly | 2 | 2009-2009 |
| Mariposa WWTP EFF-001 | effluent | Merced | 12.8 | daily | weekly | weekly | 2,191 | 2010-2026 |
| Mariposa WWTP RSW-001 | receiving_water | Merced | 12.9 | - | weekly | monthly | 1,060 | 2010-2026 |
| Inflow to San Luis Drain | drain | San Joaquin | 13.1 | - | - | daily | 856 | 2000-2025 |
| Mariposa WWTP RSW-002 | receiving_water | Merced | 13.1 | - | weekly | monthly | 903 | 2010-2026 |
| SAN LUIS DR SITE A NR S DOS PALOS CA | drain | San Joaquin | 13.1 | daily | - | - | - | 1998-2002 |
| TILE DRAIN DPS4616 | drain | San Joaquin | 13.5 | - | - | annual | 7 | 1998-2002 |
| Merced WWTF R-001D2 | receiving_water | San Joaquin | 14.2 | - | weekly | weekly | 366 | 2011-2014 |
| Merced WWTF R-001D1 | receiving_water | San Joaquin | 14.7 | - | weekly | weekly | 356 | 2011-2014 |
| Rice Drain @ Mallard Road | drain | San Joaquin | 14.7 | - | reported | - | 1 | 2000-2000 |
| Bear Valley WWTF EFF-001 | effluent | Stanislaus | 15.1 | weekly | monthly | monthly | 607 | 2016-2026 |
| Merced WWTF R-001U2 | receiving_water | San Joaquin | 15.7 | - | weekly | weekly | 262 | 2011-2014 |
| Drain to San Joaquin River off South Manthey Rd. | drain | San Joaquin | 16.1 | - | monthly | monthly | 33 | 2004-2005 |
| Merced WWTF R-002 | effluent | San Joaquin | 16.3 | daily | - | - | 3,123 | 2011-2020 |
| Merced WWTF R-002U1 | receiving_water | San Joaquin | 16.6 | - | monthly | monthly | 1,064 | 2011-2026 |
| TILE DRAIN HMH7516 | drain | San Joaquin | 17.1 | - | quarterly | quarterly | 42 | 1998-2002 |
| Panoche Drain near Dos Palos | drain | San Joaquin | 17.2 | sub-daily | quarterly | quarterly | 45 | 1964-2004 |
| TILE DRAIN HMH7016 | drain | San Joaquin | 17.3 | - | - | reported | 1 | 2002-2002 |
| Ag Drain on Pescadero Tr., PP. No. 3 | drain | San Joaquin | 17.4 | - | irregular | irregular | 6 | 1987-1994 |
| Upper Roberts Island Drain | drain | San Joaquin | 17.5 | - | monthly | monthly | 290 | 2014-2025 |
| TILE DRAIN DPS2535 | drain | San Joaquin | 18.2 | - | quarterly | quarterly | 28 | 1998-2002 |
| Tracy WWTP RSW-001 | receiving_water | San Joaquin | 18.5 | - | weekly | monthly | 366 | 2010-2026 |
| General Electric GWCS EFF-001 | effluent | Merced | 20.9 | daily | quarterly | quarterly | 3,650 | 2016-2026 |
| General Electric GWCS EFF-003 | effluent | Merced | 20.9 | daily | quarterly | annual | 923 | 2013-2015 |
| Camp 13 Drain | drain | San Joaquin | 21.0 | - | - | weekly | 89 | 2000-2013 |
| TILE DRAIN DPS3465 | drain | San Joaquin | 21.2 | - | quarterly | quarterly | 39 | 1998-2002 |
| Russell Ave. Drain at San Luis Canal | drain | San Joaquin | 21.8 | - | daily | - | 2 | 2006-2006 |
| Charleston Drain @ CCID Main | drain | San Joaquin | 22.5 | - | irregular | - | 2 | 2003-2013 |
| TILE DRAIN DPS1367 | drain | San Joaquin | 23.3 | - | quarterly | quarterly | 49 | 1998-2002 |

## Imports, exports, and returns
Imported water describes the Delta-Mendota Canal and not the Turlock Canal, which moves Tuolumne water that stayed in the basin. Four roles, each a different term in a balance.

- **import.** Delta water pumped into the basin. Adds water and salt from outside the basin.
- **export.** Basin water leaving. Removes water and salt.
- **diversion.** Basin water moved within the basin. Turlock Canal is TID's diversion from the Tuolumne at La Grange and moves Tuolumne water, not Delta water.
- **return.** Irrigation water going back to a river, concentrated by use. These belong beside the agricultural drains.

Canals do not close a gap in the river tables above, because a canal beside the river does not measure the river's temperature there. They appear here because the flux through them is a term in the balance: the Delta-Mendota Canal's delivery to Mendota Pool enters the San Joaquin on the mainstem. `mainstem km` holds a value where the link sits on the San Joaquin itself.

Mendota Pool is worth reading closely, because the flux and the quality come from different places in different decades. `Delta Mendota Canal to Mendota Pool` gages the flow of the delivery, with a discharge record from 1969 to 1990. Its `last` year in the table below, 2009, comes from its stage record. It sits 1.1 km from the San Joaquin, farther than the 500 m that puts a station on the mainstem axis, so its `mainstem km` cell is blank. CHECK 20 and CHECK 21 log the temperature and conductance of the arriving water with a continuous monitor, temperature from 1999 and conductance from 1988, both to 2026, and measure no flow. 0 of the 3 stations record both the flow and the quality continuously. The discharge record ends in 1990, before the continuous temperature record at CHECK 20 and CHECK 21 starts in 1999, so an inflow boundary after 1990 needs the flow from Reclamation's CVO reports.

| conveyance | role | reach | km | mainstem km | flow | temp | EC | last |
| --- | --- | --- | ---: | ---: | :-: | :-: | :-: | ---: |
| CHECK 21 | import | San Joaquin | 0.5 | 212.7 | - | sub-daily | sub-daily | 2026 |
| Delta Mendota Canal at Bass Avenue | import | San Joaquin | 0.5 | 212.6 | - | reported | reported | 2018 |
| Delta Mendota Canal to Mendota Pool | import | San Joaquin | 1.1 |  | daily | annual | annual | 2009 |
| CHECK 20 | import | San Joaquin | 1.6 |  | - | sub-daily | sub-daily | 2026 |
| Gianelli WQ Station near Pumping Plant | import | Stanislaus | 3.5 |  | - | monthly | weekly | 2016 |
| San Luis Canal @ Henry Miller Road | import | San Joaquin | 6.7 |  | - | weekly | - | 2014 |
| Delta Mendota Canal at DPWD | import | San Joaquin | 7.5 |  | - | monthly | - | 2024 |
| San Luis Canal Upstream of Splits | import | San Joaquin | 9.6 |  | - | monthly | weekly | 2017 |
| San Luis Canal at Hwy 152 (Site L3)  | import | San Joaquin | 12.1 |  | - | monthly | - | 2024 |
| Delta Mendota Canal mi 67.15, McCabe Rd | import | San Joaquin | 21.4 |  | - | monthly | monthly | 2002 |
| DMC@McCabe Rd Nr. Check 12 | import | San Joaquin | 21.5 |  | - | monthly | monthly | 2001 |
| CAL AQUEDUCT CHECK 12 (KA006633) | import | San Joaquin | 23.2 |  | sub-daily | sub-daily | sub-daily | 2026 |
| CAL AQUEDUCT CHECK 13 (KA007089) | import | San Joaquin | 24.8 |  | - | sub-daily | sub-daily | 2026 |
| California Aqueduct at Check 13 | import | San Joaquin | 24.9 |  | - | reported | reported |  |
| CHECK 13 (O'NEILL INTAKE) | import | San Joaquin | 25.0 |  | - | sub-daily | sub-daily | 2026 |
| O'NEILL FOREBAY @ GIANELLI PUMPING PLANT | import | San Joaquin | 28.8 |  | - | sub-daily | sub-daily | 2026 |
| PACHECO PUMPING PLANT (SLR00000) | import | San Joaquin | 34.7 |  | - | sub-daily | sub-daily | 2026 |
| FRIANT-KERN CN A FRIANT CA | export | San Joaquin | 0.3 | 311.6 | daily | - | - | 2023 |
| Friant Kern Canal at Millerton Dam | export | San Joaquin | 0.6 |  | - | reported | reported | 2008 |
| BIG C PH NO 4 NR AUBERRY CA | diversion | San Joaquin | 0.0 | 357.3 | daily | - | - | 2025 |
| COMBINED FLOW MODESTO CN PLUS TURLOCK CN CA | diversion | Tuolumne | 0.0 |  | daily | - | - | 2026 |
| DONNELL PH NR STRAWBERRY CA | diversion | Stanislaus | 0.0 |  | daily | - | - | 2020 |
| KERCKHOFF PH NR AUBERRY CA | diversion | San Joaquin | 0.0 | 339.1 | daily | - | - | 2025 |
| NEW MELONES PP BL NEW MELONES DAM NR SONORA CA | diversion | Stanislaus | 0.0 |  | daily | - | - | 2008 |
| SOUTH SAN JOAQUIN CANAL | diversion | Stanislaus | 0.0 |  | daily | - | - | 2026 |
| TULLOCH PH NR KNIGHTS FERRY CA | diversion | Stanislaus | 0.0 |  | daily | - | - | 2020 |
| TULLOCK POWERHOUSE TAILRACE | diversion | Stanislaus | 0.0 |  | - | sub-daily | - | 2010 |
| BEARDSLEY PH NR STRAWBERRY CA | diversion | Stanislaus | 0.1 |  | daily | - | - | 2020 |
| BIG C PH NO 3 NR SHAVER LK CA | diversion | San Joaquin | 0.1 | 375.5 | daily | - | - | 2025 |
| BIG C PH NO 8 NR BIG CREEK CA | diversion | San Joaquin | 0.1 | 385.0 | daily | - | - | 2025 |
| EXCHEQUER PH A EXCHEQUER CA | diversion | Merced | 0.1 |  | daily | - | - | 2025 |
| JW SOUTHERN PP A SND BAR DIV DAM NR LNG BRN CA | diversion | Stanislaus | 0.1 |  | daily | - | - | 2020 |
| MID CANAL AT LA GRANGE | diversion | Tuolumne | 0.1 |  | sub-daily | - | - | 2016 |
| NEW MELONES POWERHOUSE TAILRACE | diversion | Stanislaus | 0.1 |  | - | sub-daily | - | 2011 |
| S SAN JOAQUIN CN NR KNIGHTS FERRY CA | diversion | Stanislaus | 0.1 |  | daily | - | - | 2025 |
| STANISLAUS PP NR HATHAWAY PINES CA | diversion | Stanislaus | 0.1 |  | daily | - | - | 2025 |
| STOCKTON EAST TUNNEL | diversion | Stanislaus | 0.1 |  | daily | - | - | 2001 |
| TID CANAL AT LA GRANGE | diversion | Tuolumne | 0.1 |  | sub-daily | - | - | 2016 |
| COLLIERVILLE PP NR MURPHYS CA | diversion | Stanislaus | 0.2 |  | daily | - | - | 2025 |
| Main Canal at Bass Avenue | diversion | San Joaquin | 0.2 | 212.4 | - | reported | reported | 2018 |
| OAKDALE CN NR KNIGHTS FERRY CA | diversion | Stanislaus | 0.2 |  | daily | - | - | 2026 |
| TULLOCH DAM SPILLWAY | diversion | Stanislaus | 0.2 |  | daily | sub-daily | - | 2026 |
| TURLOCK CN NR LA GRANGE CA | diversion | Tuolumne | 0.2 |  | sub-daily | - | - | 2026 |
| KERCKHOFF PH NO 2 A MILLERTON LK NR AUBERRY CA | diversion | San Joaquin | 0.3 | 336.3 | daily | annual | annual | 2025 |
| Langworth Pipeline | diversion | Stanislaus | 0.3 |  | - | quarterly | - | 2008 |
| MADERA CN A FRIANT CA | diversion | San Joaquin | 0.3 | 311.8 | daily | - | - | 2023 |
| NORTHSIDE CN A MERCED FALLS CA | diversion | Merced | 0.3 |  | daily | - | - | 1994 |
| SAN JOAQUIN PH NO1 NR AUBERRY (WISHON PH) CA | diversion | San Joaquin | 0.3 | 355.2 | daily | - | - | 2025 |
| MAMMOTH POOL PP NR BIG CREEK CA | diversion | San Joaquin | 0.4 | 387.2 | daily | - | - | 2025 |
| MODESTO CN NR LA GRANGE CA | diversion | Tuolumne | 0.5 |  | sub-daily | - | - | 2026 |
| SAN JOAQUIN PH NO1A NR AUBERRY CA | diversion | San Joaquin | 0.5 | 369.9 | daily | - | - | 2025 |
| Ceres Main Canal at Faith Home and Hatch Road | diversion | Tuolumne | 0.6 |  | - | monthly | - | 2007 |
| MID Lateral 6/8 @ Dunn Rd. | diversion | Stanislaus | 0.6 |  | - | monthly | - | 2003 |
| S SAN JOAQUIN MAIN CN BL DIV PT NR KNIGHTS CA | diversion | Stanislaus | 0.6 |  | daily | - | - | 1989 |
| TID Lower Lateral 2 @ Grayson | diversion | San Joaquin | 0.6 |  | - | monthly | - | 2010 |
| STANISLAUS POWERHOUSE IN STAN. CANAL | diversion | Stanislaus | 0.8 |  | - | sub-daily | - | 2011 |
| STANISLAUS TU A OUTLET CA | diversion | Stanislaus | 1.1 |  | daily | - | - | 1993 |
| Lateral 5 at Carpenter Road | diversion | Tuolumne | 1.4 |  | - | quarterly | - | 2008 |
| CHERRY C CN NR EARLY INTAKE CA | diversion | Tuolumne | 1.5 |  | daily | - | - | 1996 |
| Highline Canal @ Hwy 99 | diversion | Merced | 1.9 |  | - | monthly | - | 2025 |
| MID Lateral 4 @ Paradise Rd. | diversion | San Joaquin | 2.0 |  | - | monthly | - | 2003 |
| TID Lateral 6&7 @ Central | diversion | San Joaquin | 2.1 |  | - | monthly | - | 2004 |
| Lateral 6 and 7 @ Central Ave | diversion | San Joaquin | 2.2 |  | - | monthly | - | 2025 |
| BIG C PH NO 2 NR BIG CREEK CA | diversion | San Joaquin | 2.3 |  | daily | - | - | 2025 |
| Lateral 6 at Central Avenue | diversion | San Joaquin | 2.3 |  | - | monthly | - | 2007 |
| Lateral 2 1/2 near Keyes Rd | diversion | San Joaquin | 2.6 |  | - | quarterly | - | 2025 |
| PHILADELPHIA CN NR STRAWBERRY CA | diversion | Stanislaus | 3.7 |  | daily | - | - | 2025 |
| Highline Canal @ Lombardy Rd | diversion | Merced | 4.0 |  | - | monthly | - | 2015 |
| Spillway Outlet | diversion | Tuolumne | 4.3 |  | - | annual | annual | 2015 |
| TID Lateral #2 on Service Road | diversion | Tuolumne | 4.3 |  | - | monthly | - | 2007 |
| Lateral 5 1/2 @ South Blaker Rd | diversion | San Joaquin | 4.4 |  | - | monthly | - | 2025 |
| West Stanislaus Main Canal @ Hamilton | diversion | San Joaquin | 4.5 |  | - | irregular | - | 2010 |
| CANAL C A OAKDALE RD | diversion | Merced | 4.7 |  | - | annual | annual | 2001 |
| McCoy Lateral @ Hwy 140 | diversion | San Joaquin | 4.7 |  | - | quarterly | - | 2023 |
| Howard Lateral @ Hwy 140 | diversion | San Joaquin | 5.0 |  | - | quarterly | - | 2025 |
| CCID Main Canal @ JT Crow Rd. | diversion | San Joaquin | 5.4 |  | - | quarterly | - | 2012 |
| SAN JOAQUIN PH NO2 NR NORTH FORK CA | diversion | San Joaquin | 5.7 |  | daily | - | - | 2025 |
| Lateral 3 Downstream of Hwy 99 | diversion | Tuolumne | 5.8 |  | - | daily | - | 2009 |
| Upper Lateral 2 1/2 E of Walnut Road | diversion | Tuolumne | 5.9 |  | - | reported | - | 2007 |
| BEAVER C DIV TO MCKAYS POINT RES NR ARNOLD CA | diversion | Stanislaus | 6.1 |  | daily | - | - | 2025 |
| Lateral 7 Downstream of Hwy 99 | diversion | Stanislaus | 6.3 |  | - | daily | - | 2009 |
| Santa Fe Canal at Gun Club Road (WSJRWC) | diversion | San Joaquin | 6.7 |  | - | monthly | - | 2023 |
| NEW SPICER MDW DAM PP A NSMD NR B MDW CA | diversion | Stanislaus | 7.5 |  | daily | - | - | 2025 |
| UTICA CN NR AVERY CA | diversion | Stanislaus | 7.5 |  | daily | - | - | 1989 |
| BIG C PH NO 1 AT BIG CREEK CA | diversion | San Joaquin | 8.0 |  | daily | - | - | 2025 |
| Lateral 3 along East Taylor Rd | diversion | Tuolumne | 8.2 |  | - | monthly | - | 2012 |
| LK ELEANOR DIV TO CHERRY LAKE NR HETCH HETCHY CA | diversion | Tuolumne | 8.4 |  | sub-daily | - | - | 2026 |
| NF STANISLAUS DIV TU OL BL HOBART C NR NSMD CA | diversion | Stanislaus | 8.8 |  | daily | - | - | 1994 |
| NF STANISLAUS R DIV TUNNEL NR BIG MDW CA | diversion | Stanislaus | 8.8 |  | daily | - | - | 2025 |
| Santa Fe Canal @ Weir | diversion | San Joaquin | 9.2 |  | - | - | weekly | 2013 |
| Santa Fe Canal 150' north of SLC & SFC Inters | diversion | San Joaquin | 9.4 |  | - | monthly | - | 2017 |
| EASTWOOD PP AB SHAVER LK NR BIG CREEK CA | diversion | San Joaquin | 9.6 |  | daily | - | - | 2025 |
| TUOLUMNE CN NR LONG BARN CA | diversion | Stanislaus | 9.7 |  | daily | - | - | 2026 |
| HUNTINGTON-SHAVER CONDUIT OTLT NR SHAVER LK CA | diversion | San Joaquin | 10.4 |  | daily | - | - | 1985 |
| Canal Creek @ West Bellevue Rd | diversion | Merced | 11.4 |  | - | quarterly | - | 2025 |
| El Nido Canal at W. Washington Road | diversion | San Joaquin | 11.6 |  | - | monthly | - | 2007 |
| SAN JOAQUIN PH NO3 NR NORTH FORK CA | diversion | San Joaquin | 11.6 |  | daily | - | - | 2025 |
| Santa Fe Canal at Hwy 152 (Site M3)  | diversion | San Joaquin | 11.7 |  | - | monthly | - | 2024 |
| Main Canal at Badger Flat Road | diversion | San Joaquin | 14.5 |  | - | reported | - | 2004 |
| BROWNS C CN AT BASS LAKE CA | diversion | San Joaquin | 15.2 |  | daily | - | - | 2026 |
| PGE NO 3 CONDUIT NR BASS LAKE CA | diversion | San Joaquin | 15.5 |  | daily | - | - | 2026 |
| Agatha Canal @ Mallard Road | diversion | San Joaquin | 17.2 |  | - | - | weekly | 2013 |
| BLACK RASCAL DIVERSION | diversion | Merced | 17.9 |  | sub-daily | - | - | 2026 |
| Fairfield Canal at Olive Avenue | diversion | Merced | 19.2 |  | - | monthly | - | 2007 |
| SOQUEL DIV NR SUGAR PINE CA | diversion | San Joaquin | 22.2 |  | daily | - | - | 1977 |
| Livingston Canal at Cressey Way | diversion | San Joaquin | 22.4 |  | - | monthly | - | 2007 |
| Livingston Canal at Walnut Ave | diversion | San Joaquin | 22.7 |  | - | daily | - | 2008 |
| MONO C CONDUIT NR MONO HOT SPRINGS CA | diversion | San Joaquin | 22.7 |  | daily | - | - | 2025 |
| Livingston Canal at Wilton Way | diversion | San Joaquin | 23.2 |  | - | daily | - | 2008 |
| MONO BEAR CONDUIT NR MONO HOT SPRINGS CA | diversion | San Joaquin | 23.7 |  | daily | - | - | 2025 |
| BIG C DIV NR FISH CAMP CA | diversion | Merced | 25.4 |  | sub-daily | - | - | 2012 |
| BIG CK DIVERSION NR FISH CAMP | diversion | Merced | 25.4 |  | sub-daily | - | - | 2026 |
| BEAR C CONDUIT NR LAKE THOMAS A EDISON CA | diversion | San Joaquin | 26.0 |  | daily | - | - | 2025 |
| WARD TUNNEL A INTAKE A FLORENCE LAKE CA | diversion | San Joaquin | 30.1 |  | daily | - | - | 2025 |
| Fancher Lateral @ Head | diversion | Merced | 30.4 |  | - | annual | - | 2008 |
| Newman Wasteway above SJR | return | San Joaquin | 0.0 | 79.5 | - | reported | - | 2000 |
| Lateral 6 Spill | return | Stanislaus | 0.2 |  | - | quarterly | - | 2009 |
| North Side Canal @ Spill to Merced R | return | Merced | 0.2 |  | - | quarterly | - | 2008 |
| Waterford L.M. Spill | return | Tuolumne | 0.2 |  | - | quarterly | - | 2009 |
| Westley Wasteway at Refuge Ponds | return | San Joaquin | 0.2 | 29.0 | - | reported | - | 2005 |
| HIGHLINE CN SPILL NR HILMAR CA | return | Merced | 0.3 |  | - | annual | - | 2008 |
| Westley Wasteway near Cox Road | return | San Joaquin | 0.3 | 29.5 | - | monthly | - | 2025 |
| Livingston Canal Spill to Merced R  | return | Merced | 0.4 |  | - | quarterly | - | 2008 |
| Lateral 5 Spill | return | Tuolumne | 0.8 |  | - | quarterly | - | 2009 |
| Lateral 1 Spill | return | Tuolumne | 0.9 |  | - | quarterly | - | 2008 |
| Spenker Spill | return | Stanislaus | 1.2 |  | - | annual | - | 2009 |
| Lateral 5 1/2 Drop 23 (Lower Spill) | return | San Joaquin | 1.6 |  | - | annual | - | 2008 |
| Lower Lateral 2 1/2 Spill | return | San Joaquin | 1.9 |  | - | quarterly | - | 2008 |
| Lateral 7 Outlet | return | Stanislaus | 2.2 |  | - | quarterly | - | 2009 |
| Newman Wasteway near Hills Ferry Road | return | San Joaquin | 2.2 |  | - | monthly | - | 2025 |
| Lateral 3 Outlet | return | Stanislaus | 3.6 |  | - | quarterly | - | 2009 |
| Westley Wasteway ~0.5mi SW Frank Cox Rd. | return | San Joaquin | 4.0 |  | - | reported | - | 2007 |
| Hodges Drop (Ceres Main Drop 32 Spill) | return | San Joaquin | 7.1 |  | - | reported | - | 2004 |
| El Nido Canal Spill to Chowchilla Slough | return | San Joaquin | 20.6 |  | - | annual | - | 2008 |

## Coverage through time
Stations with an active record in each decade. Coverage is far denser after 1990, so a calibration period before then will be thin whichever sites you choose.

A station counts for a decade its record spans only if it averaged at least 6 observations per decade. CEDEN, EDI, eSMR, and the Water Quality Portal publish a sample count and a period of record with no stated interval, and the harvest measures the frequency instead. Without that floor, two of their samples thirty years apart would count as four decades of monitoring. Only station-scope records count; see `coverage.station_level`. A row with no observation count covers every decade its span crosses. 1,270 of the 2,419 rows here have no count, from CDEC, DWR's Water Data Library, RISE, and USGS, and 1,086 of those 1,270 come off a continuous monitor, whose span is the record.

| decade   |   discharge |   specific_conductance |   water_temperature |
|:---------|------------:|-----------------------:|--------------------:|
| 1950s    |         108 |                      0 |                   0 |
| 1960s    |         155 |                      0 |                   9 |
| 1970s    |         155 |                      0 |                  18 |
| 1980s    |         173 |                     17 |                  29 |
| 1990s    |         193 |                     79 |                  86 |
| 2000s    |         188 |                    224 |                 457 |
| 2010s    |         228 |                    197 |                 429 |
| 2020s    |         215 |                     98 |                 233 |

## Co-located stations

94 stations measure temperature, specific conductance, and discharge at one site. A temperature balance needs temperature and flow at one place, and a salt balance needs conductance and flow, so a site with all three serves both. 77 of the 94 have a period when the three records overlap, from the latest of the three starts to the earliest of the three ends, and the other 17 have none.

44 of the 94 record all three with a continuous monitor, and 40 of those 44 have a period when the three continuous records overlap. Of the other 50, 40 record one or two of the three continuously and the rest as discrete samples, and 13 of those 40 have a continuous temperature record. The remaining 10 measure all three through discrete samples only, which supports a mass balance but cannot calibrate a temperature model against a diurnal cycle. `figures/08_colocation.png` draws the 44 continuous sites apart from the other 50.

`data/processed/catalog_filtered.csv` lists all 94 with the period of each record and the overlap of the temperature and conductance records. The 76 on the shortlist have the `rank` that numbers the markers on `figures/08_colocation.png`, and the other 18 fall short of the 3-year rule and have no rank.

## What each source adds
The harvest runs eight APIs and they overlap heavily by design. The portal republishes CEDEN, SWAMP, the irrigated lands coalitions, and USGS discrete samples, so the question worth answering is how much a separate harvest of those sources still finds. `unique to this source` is the number of stations that no other harvested source reports.

A station reaches this table only if it reports one of the target parameters. The Water Quality Portal's two endpoints return different things: `Station/search` returns every monitoring location in a HUC regardless of what it measures, while `Result/search` returns only this inventory's 36 characteristics. `Station/search` returned 12,152 locations in this run. The harvest removes 489 of them for a missing coordinate, a synthetic test-site number, or an organization outside water monitoring, and drops 10,773 of the rest because they report 0 of this inventory's 36 characteristics. Keeping those 10,773 would put them into the catalog with no parameter attached, and the figures would then show them as stations with no data, so `sources/wqp.py` drops them at the harvest and logs the count.

| source    |   records |   stations |   unique to this source |   also in another source |
|:----------|----------:|-----------:|------------------------:|-------------------------:|
| wqp       |       606 |        544 |                     322 |                      222 |
| ceden     |       509 |        478 |                     303 |                      175 |
| usgs      |       313 |        307 |                     210 |                       97 |
| cdec      |       244 |        235 |                     142 |                       93 |
| dwr_wdl   |        75 |         67 |                      27 |                       40 |
| esmr      |        57 |         51 |                      51 |                        0 |
| edi       |        22 |         15 |                       6 |                        9 |
| usbr_rise |         8 |          8 |                       6 |                        2 |

A source can add almost no new locations and still be worth harvesting. EDI adds 6, because the Interagency Ecological Program's continuous stations are the DWR and USGS gages CDEC already publishes. What it adds is length. These are the stations where EDI's earliest series starts 2 years or more before every other record this inventory holds for the same parameter. The comparison runs continuous against continuous and discrete against discrete, so a discrete sample does not extend a sonde record. Station-scope records only; see `coverage.station_level`.

| name                             | parameter            | kind       | dataset    | frequency   | record    |   previously_from |   years_earlier |
|:---------------------------------|:---------------------|:-----------|:-----------|:------------|:----------|------------------:|----------------:|
| PARADISE CUT UPSTREAM            | specific_conductance | continuous | edi.2180.2 | sub-daily   | 1999-2025 |              2015 |            15.7 |
| PARADISE CUT UPSTREAM            | water_temperature    | continuous | edi.2180.2 | sub-daily   | 1999-2025 |              2015 |            15.7 |
| SAN JOAQUIN RIVER ABOVE DOS REIS | specific_conductance | continuous | edi.2180.2 | sub-daily   | 1999-2025 |              2014 |            14.5 |
| SAN JOAQUIN RIVER ABOVE DOS REIS | water_temperature    | continuous | edi.2180.2 | sub-daily   | 1999-2025 |              2013 |            14.1 |
| OLD RIVER ABOVE DOUGHTY CUT      | specific_conductance | continuous | edi.2180.2 | sub-daily   | 1999-2025 |              2013 |            13.7 |
| OLD RIVER ABOVE DOUGHTY CUT      | water_temperature    | continuous | edi.2180.2 | sub-daily   | 1999-2025 |              2013 |            13.5 |

## Units that do not match the quantity

107 of 8,122 station-parameter rows use a unit that does not convert to the canonical unit in `config/parameters.yml`. 43 are a different quantity filed under the parameter's name, 40 have no unit, 16 have a unit string outside the tables in `sjrwq/units.py`, and 8 join more than one reporting basis in one row. A time-series pull keyed on the parameter name would mix the 43 rows of a different quantity in with the rest. `station_parameter.parquet` has each row's conversion factor, or the reason it has none, in `unit_factor` and `unit_note`.

| parameter              | unit     | unit_note                                                           |   rows |
|:-----------------------|:---------|:--------------------------------------------------------------------|-------:|
| suspended_solids       | lb/day   | mass load per unit time, not a concentration                        |     13 |
| water_temperature      | degree   | unrecognized unit 'degree'                                          |     11 |
| ammonia                |          | source reported no unit                                             |     10 |
| ammonia                | lb/day   | mass load per unit time, not a concentration                        |      8 |
| phosphorus             |          | source reported no unit                                             |      7 |
| chlorophyll_a          |          | source reported no unit                                             |      6 |
| nitrate                |          | source reported no unit                                             |      6 |
| diversion              | AF       | a volume unit reported for a flow rate measurement                  |      5 |
| sulfate                | mg/L     | S and SO4 bases joined in one row, so no single factor applies      |      4 |
| selenium               |          | source reported no unit                                             |      4 |
| total_dissolved_solids | tons/day | mass load per unit time, not a concentration                        |      3 |
| chlorophyll_a          | ug/cm2   | areal, a benthic measurement rather than a water column one         |      2 |
| chlorophyll_a          | FLUORO   | relative fluorescence, uncalibrated                                 |      2 |
| total_nitrogen         |          | source reported no unit                                             |      2 |
| suspended_solids       | %        | percent of total or of saturation, not a concentration              |      2 |
| phosphorus             | lb/day   | mass load per unit time, not a concentration                        |      2 |
| selenium               | lb/day   | mass load per unit time, not a concentration                        |      2 |
| nitrate                | mg/L     | N and NO3 bases joined in one row, so no single factor applies      |      2 |
| alkalinity             | mg/L     | CaCO3 and HCO3 bases joined in one row, so no single factor applies |      2 |
| ph                     | mV       | electrode potential, not a pH reading                               |      1 |

13 further combinations are in `station_parameter.parquet` under `unit_convertible` and `unit_note`.

## Known holes in this inventory

These are gaps in the harvest itself, not in the underlying monitoring network. This section lists them so a retrieval failure does not pass for an absence of data.

**The harvest reaches EDI, and 76 of its 92 EDI rows have a dataset period rather than a station one.** PASTA returns HTTP 403 for an anonymous request to its search, revision-list, and metadata endpoints. Two routes get past that: DataONE indexes EDI as a member node and serves the same metadata without an account, and PASTA itself accepts an account token sent as an `edi-token` cookie. `sources/edi.py` supports both. Of the 4 EDI packages with rows in this inventory, 1 publishes one table per station, so only in rows from `edi.2180` does the publisher pair each station with its parameters. The other 3 publish a wide table keyed by a station column and list their stations separately, so the pairing is an inference in `sources/edi.py`, the sample count stays null, and the reporting interval is a table's row count spread evenly over its stations. Those rows have `period_scope = dataset` in `station_parameter.parquet`, and `coverage.station_level` keeps them out of the calibration catalog, the availability timeline, the decade table, the record-extension table, and the co-located overlap counts above. The mainstem, boundary, and reservoir searches test the end date of every row, dataset rows included. Six decades of Delta water quality, `edi.731`, has 60 of the 76 dataset rows, and lists the same 10 parameters at each of its 6 stations, chloride among them at fish-survey stations, where the monitoring program behind the database takes no chloride sample. The package's period of record, 1959 to 2022, covers the whole database, so without the filter the conductance record at Vernalis would date from 1959 to 2026, where the continuous record starts in 1985. Settling the pairing needs the station column from the data file, which is a download this inventory does not do.

**The harvest holds a flagged depth profile at 0 of the 6 rim dams.** RISE publishes a `hasProfile` flag on each of its 136 series in the basin and sets it on 0. RISE publishes series within 5 km of 2 of the 6 dams (Millerton and New Melones). At those 2 the absence is a checked finding, and at the other 4 the harvest has no RISE series to check. The profiles exist, and they move by email: Reclamation, the USACE Sacramento District, the Turlock and Modesto Irrigation Districts, and Merced Irrigation District hold the profiles.

**Stockton's plant sits outside the domain.** eSMR lists it under the facility name "Wastewater Recovery Center", with 126,830 records of this inventory's parameters from 12 monitoring locations since 2010. That is 2.4 times the 53,352 of "Turlock City, Turlock Regional Water Quality Control Facility", the facility with the most records inside the domain. All 12 locations fall in the San Joaquin Delta subbasin (18040003), outside `huc8_in_scope` in `config/domain.yml`, so the study-area clip removes the plant. The records are already in `data/interim`: move 18040003 into `huc8_in_scope` and re-run.

**Source data errors handled here.** CDEC lists a stage record at station SMN beginning 03/01/0010, a water temperature record at station MLK ending before it starts, and a water temperature record at station PRI ending before it starts. Station PRI is outside `catalog.csv`. The plausibility guard in `sjrwq.inventory` nulls the start date of the records at MLK and SMN and logs each date it nulls. It keeps both rows. USGS publishes synthetic records through the portal under invented site numbers like 123123123123123, named "ITT test site N"; they hold no results, and the harvest drops them.

**Source data errors left as published.** This inventory does not silently correct an upstream coordinate or classification, and these 2 are worth knowing about.

- DWR publishes station `CALWR_WQX-DES WQ 40`, "Sacramento River @ Martinez", at 121.14 W. Martinez is at 122.14 W, so a digit is wrong and the coordinate lands in the Calaveras subbasin instead of on the Carquinez Strait. The station is outside `catalog.csv`, because the Calaveras is outside the study area.
- DWR publishes stations whose names are California State Well Numbers, such as `05N08E26P001M`, and gives them a portal `location_type` of "Other-Surface Water", which stays as published in `data/interim/wqp_stations.gpkg`. 27 reach `catalog.csv`, with 1 to 9 specific conductance samples each, and 7 reach the shortlist in `catalog_filtered.csv`. `sjrwq.classify` matches the name pattern and sets their `channel_class` to `groundwater`, so `_rank_shortlist` numbers the 7 among the off-channel stations at the end of the shortlist, at ranks 346 to 390 of 412.

A cross-check bounds how common a misplaced coordinate is. Of the 312 portal stations in `data/interim/wqp_stations.gpkg` that publish their own HUC12 code and fall inside a HUC8 polygon in `domain.gpkg`, the coordinate falls in a different HUC8 than the published one for 0 of them.
