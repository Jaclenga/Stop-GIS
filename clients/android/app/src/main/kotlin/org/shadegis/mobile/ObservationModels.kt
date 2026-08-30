package org.shadegis.mobile

import com.squareup.moshi.Json
import com.squareup.moshi.JsonClass
import java.time.Instant
import java.util.UUID

const val OBSERVATION_SCHEMA_VERSION = 1

@JsonClass(generateAdapter = false)
data class Stop(
    @Json(name = "stop_id") val stopId: String,
    @Json(name = "stop_name") val stopName: String,
    @Json(name = "stop_lat") val stopLat: Double,
    @Json(name = "stop_lon") val stopLon: Double,
    @Json(name = "route_labels") val routeLabels: List<String>,
)

enum class ShadeCoverage(val label: String) {
    @Json(name = "No Shade")
    NO_SHADE("No Shade"),

    @Json(name = "Limited Shade")
    LIMITED_SHADE("Limited Shade"),

    @Json(name = "Significant Shade")
    SIGNIFICANT_SHADE("Significant Shade"),
}

enum class ShadeSource(val label: String) {
    @Json(name = "Natural")
    NATURAL("Natural"),

    @Json(name = "Purpose-built")
    PURPOSE_BUILT("Purpose-built"),

    @Json(name = "Incidental")
    INCIDENTAL("Incidental"),
}

@JsonClass(generateAdapter = false)
data class ShadeObservation(
    @Json(name = "schema_version") val schemaVersion: Int,
    @Json(name = "observation_id") val observationId: String,
    @Json(name = "stop_id") val stopId: String,
    @Json(name = "stop_name") val stopName: String,
    @Json(name = "stop_lat") val stopLat: Double?,
    @Json(name = "stop_lon") val stopLon: Double?,
    @Json(name = "route_labels") val routeLabels: List<String>,
    @Json(name = "photo_uri") val photoUri: String,
    @Json(name = "shade_coverage") val shadeCoverage: ShadeCoverage,
    @Json(name = "shade_sources") val shadeSources: List<ShadeSource>,
    @Json(name = "assessment_values") val assessmentValues: Map<String, List<String>> = emptyMap(),
    @Json(name = "notes") val notes: String,
    @Json(name = "captured_at") val capturedAt: String,
) {
    init {
        require(schemaVersion == OBSERVATION_SCHEMA_VERSION) { "Unsupported observation schema" }
        require(observationId.isNotBlank() && observationId.length <= 256) { "Invalid observation ID" }
        require(runCatching { UUID.fromString(observationId) }.isSuccess) { "Observation ID must be a UUID" }
        require(stopId.isNotBlank() && stopId.length <= 256) { "Invalid stop ID" }
        require(stopName.isNotBlank()) { "Stop name is required" }
        require(stopLat == null || stopLat in -90.0..90.0) { "Invalid stop latitude" }
        require(stopLon == null || stopLon in -180.0..180.0) { "Invalid stop longitude" }
        require(photoUri.isNotBlank()) { "Photo URI is required" }
        require(notes.length <= MAX_NOTES_LENGTH) { "Notes are too long" }
        require(routeLabels.distinct().size == routeLabels.size) { "Route labels must be unique" }
        require(shadeSources.distinct().size == shadeSources.size) { "Shade sources must be unique" }
        require(shadeCoverage != ShadeCoverage.NO_SHADE || shadeSources.isEmpty()) {
            "No Shade cannot have shade sources"
        }
        Instant.parse(capturedAt)
    }

    companion object {
        const val MAX_NOTES_LENGTH = 500

        fun create(
            stop: Stop,
            photoUri: String,
            coverage: ShadeCoverage,
            sources: Collection<ShadeSource>,
            notes: String,
            capturedAt: Instant = Instant.now(),
            id: String = UUID.randomUUID().toString(),
        ): ShadeObservation {
            val normalizedSources = if (coverage == ShadeCoverage.NO_SHADE) {
                emptyList()
            } else {
                sources.distinct()
            }
            return ShadeObservation(
                schemaVersion = OBSERVATION_SCHEMA_VERSION,
                observationId = id,
                stopId = stop.stopId.trim(),
                stopName = stop.stopName.trim(),
                stopLat = stop.stopLat,
                stopLon = stop.stopLon,
                routeLabels = stop.routeLabels.map { it.trim() }.filter { it.isNotEmpty() }.distinct(),
                photoUri = photoUri,
                shadeCoverage = coverage,
                shadeSources = ShadeSource.entries.filter(normalizedSources::contains),
                assessmentValues = mapOf(
                    "shade_coverage" to listOf(
                        when (coverage) {
                            ShadeCoverage.NO_SHADE -> "none"
                            ShadeCoverage.LIMITED_SHADE -> "limited"
                            ShadeCoverage.SIGNIFICANT_SHADE -> "significant"
                        }
                    ),
                    "shade_source" to ShadeSource.entries.filter(normalizedSources::contains).map {
                        when (it) {
                            ShadeSource.NATURAL -> "natural"
                            ShadeSource.PURPOSE_BUILT -> "purpose_built"
                            ShadeSource.INCIDENTAL -> "incidental"
                        }
                    },
                ),
                notes = notes.trim(),
                capturedAt = capturedAt.toString(),
            )
        }
    }
}

@JsonClass(generateAdapter = false)
data class ObservationStore(
    @Json(name = "schema_version") val schemaVersion: Int = OBSERVATION_SCHEMA_VERSION,
    @Json(name = "observations") val observations: List<ShadeObservation>,
)

fun sampleStops(): List<Stop> = listOf(
    Stop("STOP001", "Central Ave & Main St", 27.96, -82.45, listOf("2", "14")),
    Stop("STOP002", "East Tampa Transit Center", 27.95, -82.44, listOf("6", "12")),
    Stop("STOP003", "Ybor City Station", 27.97, -82.43, listOf("8", "19")),
)
