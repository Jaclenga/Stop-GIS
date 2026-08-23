package org.shadegis.mobile

import android.content.Context
import android.util.AtomicFile
import com.squareup.moshi.JsonClass
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.OutputStreamWriter
import java.time.Instant
import java.util.UUID

private val CURRENT_STORE_PREFIX = Regex("^\\s*\\{\\s*\"schema_version\"\\s*:")

data class LoadResult(
    val observations: List<ShadeObservation>,
    val migratedLegacyData: Boolean = false,
    val skippedLegacyRecords: Int = 0,
)

class ObservationCodec(
    private val moshi: Moshi = Moshi.Builder().addLast(KotlinJsonAdapterFactory()).build(),
) {
    private val storeAdapter = moshi.adapter(ObservationStore::class.java).indent("  ")
    private val legacyAdapter = moshi.adapter(LegacyObservation::class.java)
    private val objectAdapter = moshi.adapter(Map::class.java)

    fun encode(observations: List<ShadeObservation>): String =
        storeAdapter.toJson(ObservationStore(observations = observations))

    fun decode(json: String): LoadResult {
        if (json.isBlank()) return LoadResult(emptyList())

        val rootKeys = runCatching { objectAdapter.fromJson(json)?.keys.orEmpty() }.getOrDefault(emptySet())
        val isVersionedStore = CURRENT_STORE_PREFIX.containsMatchIn(json) ||
            "schema_version" in rootKeys || "observations" in rootKeys
        if (isVersionedStore) {
            val store = requireNotNull(storeAdapter.fromJson(json)) { "Observation file is empty" }
            require(store.schemaVersion == OBSERVATION_SCHEMA_VERSION) {
                "Unsupported store schema version ${store.schemaVersion}"
            }
            require(store.observations.distinctBy(ShadeObservation::observationId).size == store.observations.size) {
                "Observation IDs must be unique"
            }
            return LoadResult(store.observations.sortedByDescending { Instant.parse(it.capturedAt) })
        }

        var skipped = 0
        val migrated = json.lineSequence().filter(String::isNotBlank).mapNotNull { line ->
            try {
                legacyAdapter.fromJson(line)?.toCurrent()
            } catch (_: Exception) {
                skipped += 1
                null
            }
        }.toList()
        return LoadResult(migrated.sortedByDescending { Instant.parse(it.capturedAt) }, true, skipped)
    }
}

class ObservationRepository(
    context: Context,
    private val codec: ObservationCodec = ObservationCodec(),
) {
    private val storeFile = AtomicFile(File(context.filesDir, FILE_NAME))

    suspend fun load(): LoadResult = withContext(Dispatchers.IO) {
        val baseFile = storeFile.baseFile
        if (!baseFile.exists()) return@withContext LoadResult(emptyList())
        val result = codec.decode(storeFile.openRead().bufferedReader(Charsets.UTF_8).use { it.readText() })
        if (result.migratedLegacyData && result.skippedLegacyRecords == 0) write(result.observations)
        result
    }

    suspend fun save(observation: ShadeObservation): List<ShadeObservation> = withContext(Dispatchers.IO) {
        val existing = if (storeFile.baseFile.exists()) {
            val result = codec.decode(storeFile.openRead().bufferedReader(Charsets.UTF_8).use { it.readText() })
            require(result.skippedLegacyRecords == 0) {
                "Resolve unreadable legacy records before saving"
            }
            result.observations
        } else {
            emptyList()
        }
        val updated = (listOf(observation) + existing)
            .distinctBy(ShadeObservation::observationId)
            .sortedByDescending { Instant.parse(it.capturedAt) }
        write(updated)
        updated
    }

    private fun write(observations: List<ShadeObservation>) {
        val output = storeFile.startWrite()
        try {
            OutputStreamWriter(output, Charsets.UTF_8).apply {
                write(codec.encode(observations))
                flush()
            }
            storeFile.finishWrite(output)
        } catch (error: Exception) {
            storeFile.failWrite(output)
            throw error
        }
    }

    private companion object {
        const val FILE_NAME = "shade_gis_observations.json"
    }
}

@JsonClass(generateAdapter = false)
private data class LegacyObservation(
    val stopId: String,
    val stopName: String,
    val photoUri: String,
    val shadeCoverage: String,
    val shadeSources: List<String> = emptyList(),
    val notes: String = "",
    val capturedAt: String,
) {
    fun toCurrent(): ShadeObservation {
        val coverage = ShadeCoverage.entries.first { it.label == shadeCoverage }
        val sources = if (coverage == ShadeCoverage.NO_SHADE) {
            emptyList()
        } else {
            shadeSources.mapNotNull { value -> ShadeSource.entries.find { it.label == value } }.distinct()
        }
        return ShadeObservation(
            schemaVersion = OBSERVATION_SCHEMA_VERSION,
            observationId = UUID.nameUUIDFromBytes(
                "$stopId|$photoUri|$capturedAt".toByteArray(Charsets.UTF_8),
            ).toString(),
            stopId = stopId,
            stopName = stopName,
            stopLat = null,
            stopLon = null,
            routeLabels = emptyList(),
            photoUri = photoUri,
            shadeCoverage = coverage,
            shadeSources = sources,
            notes = notes,
            capturedAt = capturedAt,
        )
    }
}
