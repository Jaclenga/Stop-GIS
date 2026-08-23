# Shade-GIS Android app

Offline-first field capture for Shade-GIS observations. A surveyor selects a bus stop, takes a photo,
records shade coverage and sources, adds optional notes, and saves the observation on the device.

## Run the app

Open `android_app` in Android Studio, let Gradle sync, and run the `app` configuration on an Android 7.0
(API 24) or newer device/emulator with a camera app. The project uses JDK 17.

The repository does not currently include the Gradle wrapper, so command-line builds require local Gradle 8.1
and JDK 17 for Android Gradle Plugin 8.1.

## Storage and privacy

- Observations are stored in app-private `shade_gis_observations.json`.
- Photos are stored in app-private `files/photos`; they are not placed in the media gallery.
- Writes use Android's `AtomicFile`, so an interrupted save does not replace a valid store with a partial file.
- Android backup is disabled because observations and photos may contain field-sensitive data.
- The previous camelCase JSON-lines file is upgraded on first read. If a legacy record is unreadable, the original
  file is left untouched and saving is blocked rather than silently deleting that record. A malformed versioned
  store is also left untouched.

## Observation schema (version 1)

The file is a JSON object with `schema_version` and an `observations` array. Observation fields use the
same snake_case vocabulary as the Shade-GIS platform:

| Field | Type | Rule |
| --- | --- | --- |
| `schema_version` | integer | Must be `1`. |
| `observation_id` | string | Stable, unique UUID. |
| `stop_id`, `stop_name` | string | Required stop identity. |
| `stop_lat`, `stop_lon` | number or null | Valid WGS84 coordinate ranges; null only for migrated legacy data. |
| `route_labels` | string array | De-duplicated route labels. |
| `photo_uri` | string | URI for the durable app-private photo. |
| `shade_coverage` | enum | `No Shade`, `Limited Shade`, or `Significant Shade`. |
| `shade_sources` | enum array | `Natural`, `Purpose-built`, and/or `Incidental`. Empty when coverage is `No Shade`. |
| `notes` | string | Optional, at most 500 characters. |
| `captured_at` | string | ISO-8601 UTC instant. |

The bundled stops are placeholders in `ObservationModels.kt`. Replace `sampleStops()` with an imported study
configuration before production deployment. Backend upload/sync is not implemented yet.
