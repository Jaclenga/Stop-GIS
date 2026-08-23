package org.shadegis.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class ObservationCodecTest {
    private val codec = ObservationCodec()
    private val stop = Stop("1001", "Main St, North", 27.95, -82.45, listOf("2", "14"))

    @Test
    fun roundTripPreservesStructuredFieldsAndPunctuation() {
        val original = ShadeObservation.create(
            stop = stop,
            photoUri = "content://org.shadegis.mobile.fileprovider/photos/test.jpg",
            coverage = ShadeCoverage.LIMITED_SHADE,
            sources = listOf(ShadeSource.INCIDENTAL, ShadeSource.NATURAL),
            notes = "Bench, sign, and \"oak\".\nSecond line.",
            capturedAt = Instant.parse("2026-08-08T15:30:00Z"),
            id = "00000000-0000-0000-0000-000000000001",
        )

        val encoded = codec.encode(listOf(original))
        val decoded = codec.decode(encoded)

        assertEquals(listOf(original), decoded.observations)
        assertFalse(decoded.migratedLegacyData)
        assertTrue(encoded.contains("\"schema_version\""))
        assertTrue(encoded.contains("\"shade_coverage\""))
        assertTrue(encoded.contains("\"route_labels\""))
    }

    @Test
    fun noShadeAlwaysRemovesSources() {
        val observation = ShadeObservation.create(
            stop = stop,
            photoUri = "content://photo",
            coverage = ShadeCoverage.NO_SHADE,
            sources = listOf(ShadeSource.NATURAL),
            notes = "",
        )

        assertTrue(observation.shadeSources.isEmpty())
    }

    @Test
    fun migratesLegacyJsonLinesWithoutLosingCommas() {
        val legacy = """{"stopId":"1001","stopName":"Main St, North","photoUri":"content://photo","shadeCoverage":"Limited Shade","shadeSources":["Natural","Incidental"],"notes":"Bench, sign","capturedAt":"2026-08-08T15:30:00Z"}"""

        val result = codec.decode(legacy)

        assertTrue(result.migratedLegacyData)
        assertEquals(0, result.skippedLegacyRecords)
        assertEquals("Main St, North", result.observations.single().stopName)
        assertEquals("Bench, sign", result.observations.single().notes)
        assertEquals(listOf(ShadeSource.NATURAL, ShadeSource.INCIDENTAL), result.observations.single().shadeSources)
        assertEquals(null, result.observations.single().stopLat)
        assertEquals(null, result.observations.single().stopLon)
    }

    @Test
    fun preservesCorrectedDuplicateLegacyLinesWithUniqueStableIds() {
        val original = """{"stopId":"1001","stopName":"Main St","photoUri":"content://photo","shadeCoverage":"Limited Shade","shadeSources":["Natural"],"notes":"first","capturedAt":"2026-08-08T15:30:00Z"}"""
        val corrected = """{"stopId":"1001","stopName":"Main St","photoUri":"content://photo","shadeCoverage":"Limited Shade","shadeSources":["Natural"],"notes":"corrected","capturedAt":"2026-08-08T15:30:00Z"}"""

        val firstLoad = codec.decode("$original\n$corrected")
        val secondLoad = codec.decode("$original\n$corrected")

        assertEquals(2, firstLoad.observations.size)
        assertEquals(setOf("first", "corrected"), firstLoad.observations.map { it.notes }.toSet())
        assertEquals(2, firstLoad.observations.map { it.observationId }.toSet().size)
        assertEquals(
            firstLoad.observations.map { it.observationId },
            secondLoad.observations.map { it.observationId },
        )
    }

    @Test
    fun recordsPhotoUrisForSkippedLegacyEvidence() {
        val invalid = """{"stopId":"1001","stopName":"Main St","photoUri":"content://org.shadegis.mobile.fileprovider/photos/evidence.jpg","shadeCoverage":"Unknown","capturedAt":"2026-08-08T15:30:00Z"}"""

        val result = codec.decode(invalid)

        assertEquals(1, result.skippedLegacyRecords)
        assertEquals(
            setOf("content://org.shadegis.mobile.fileprovider/photos/evidence.jpg"),
            result.skippedLegacyPhotoUris,
        )
    }

    @Test
    fun rejectsUnknownStoreSchema() {
        val futureStore = """{"schema_version":99,"observations":[]}"""

        assertThrows(IllegalArgumentException::class.java) { codec.decode(futureStore) }
    }

    @Test
    fun recognizesVersionedStoreWhenFieldsAreReordered() {
        val reordered = """{"observations":[],"schema_version":1}"""

        val result = codec.decode(reordered)

        assertFalse(result.migratedLegacyData)
        assertTrue(result.observations.isEmpty())
    }
}
