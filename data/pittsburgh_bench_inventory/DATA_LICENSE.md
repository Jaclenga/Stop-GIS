# Data terms, licensing, and attribution

The artifacts in this directory combine records from separately governed
sources. There is no single license for the combined package, and the
repository's software license does not replace the source-data terms.

## Pittsburgh Regional Transit

The source is Pittsburgh Regional Transit (PRT), formerly named Port Authority
of Allegheny County (PAAC). PRT's official
[Developer Resources](https://www.rideprt.org/business-center/developer-resources/)
page says that use of its GIS data is subject to the
[Developer License Agreement](https://www.rideprt.org/business-center/developer-resources/developer-license-agreement/).
That agreement, rather than this package, determines permitted reuse.

The ArcGIS item metadata captured by the builder reported `CC0` for both source
layers. Because PRT's official developer page separately applies its agreement
to GIS data, this package does not represent the PRT-derived records as
unconditionally CC0. Reusers should review the live terms before redistribution.

PRT sources (accessed 2026-09-04):

- Pittsburgh Regional Transit. (2026). *PRT Stops - Current (full system)*
  [Feature layer; ArcGIS item `a29f37608eb34c3895332ff99eea9b17`].
  [Item page](https://www.arcgis.com/home/item.html?id=a29f37608eb34c3895332ff99eea9b17) ·
  [data layer](https://services3.arcgis.com/544gNI3xxlFIWuTc/arcgis/rest/services/Transit_Stops_%28system%29/FeatureServer/0).
- Pittsburgh Regional Transit. (2026). *PRT Stop Amenities - Current*
  [Feature layer; ArcGIS item `73d1faab8d3441babcd3463ef6987559`].
  [Item page](https://www.arcgis.com/home/item.html?id=73d1faab8d3441babcd3463ef6987559) ·
  [data layer](https://services3.arcgis.com/544gNI3xxlFIWuTc/arcgis/rest/services/PRT_Current_Shelter_Locations/FeatureServer/0).

The PRT agreement requires derivative versions to carry the following notice:

> Reproduced with permission granted by Port Authority of Allegheny County
> (PAAC). The information has been provided by means of a nonexclusive,
> limited, and revocable license granted by PAAC. PAAC does not guarantee the
> accuracy, adequacy, completeness, or usefulness of any information. PAAC
> provides this information "as is" without warranty of any kind, express or
> implied, including, but not limited to, warranties of merchantability or
> fitness for a particular purpose and assumes no responsibility for anyone's
> use of the information.

## OpenStreetMap

OpenStreetMap-derived element identifiers, metadata, and tags are ©
OpenStreetMap contributors and are available under the Open Database License
(ODbL) 1.0. Retain the attribution and identify the license when using or
redistributing these data. See
[OpenStreetMap copyright and license](https://www.openstreetmap.org/copyright)
and the [ODbL 1.0 legal text](https://opendatacommons.org/licenses/odbl/1-0/).

Source citation (accessed 2026-09-04):

- OpenStreetMap contributors. (2026). *OpenStreetMap* [Database]. OpenStreetMap
  Foundation. Data queried through the Overpass API. ODbL 1.0.
  https://www.openstreetmap.org/copyright

## Stop-GIS software

The `build_pittsburgh_bench_seed.py` builder and Stop-GIS software are
separately available under the repository's MIT License. The MIT License does
not relicense PRT- or OpenStreetMap-derived data.
