package org.shadegis.mobile

import android.content.Context
import android.net.Uri
import android.util.AtomicFile
import com.squareup.moshi.JsonClass
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
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
    val legacyBackupCreated: Boolean = false,
    val skippedLegacyPhotoUris: Set<String> = emptySet(),
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
        val skippedPhotoUris = mutableSetOf<String>()
        val migrated = json.lineSequence().filter(String::isNotBlank).mapNotNull { line ->
            var legacy: LegacyObservation? = null
            try {
                legacy = requireNotNull(legacyAdapter.fromJson(line)) { "Legacy observation is empty" }
                legacy.toCurrent()
            } catch (_: Exception) {
                skipped += 1
                legacy?.photoUri?.takeIf(String::isNotBlank)?.let(skippedPhotoUris::add)
                null
            }
        }.toList().withUniqueLegacyIds()
        return LoadResult(
            migrated.sortedByDescending { Instant.parse(it.capturedAt) },
            migratedLegacyData = true,
            skippedLegacyRecords = skipped,
            skippedLegacyPhotoUris = skippedPhotoUris,
        )
    }

    fun legacyPhotoUris(jsonLines: String): Set<String> =
        jsonLines.lineSequence().filter(String::isNotBlank).mapNotNull { line ->
            runCatching { legacyAdapter.fromJson(line)?.photoUri }
                .getOrNull()
                ?.takeIf(String::isNotBlank)
        }.toSet()

    private fun List<ShadeObservation>.withUniqueLegacyIds(): List<ShadeObservation> {
        val usedIds = mutableSetOf<String>()
        val occurrences = mutableMapOf<String, Int>()
        return map { observation ->
            val baseId = observation.observationId
            var occurrence = occurrences.getOrDefault(baseId, 0)
            var candidateId = baseId
            while (candidateId in usedIds) {
                occurrence += 1
                candidateId = UUID.nameUUIDFromBytes(
                    "$baseId|legacy-duplicate:$occurrence".toByteArray(Charsets.UTF_8),
                ).toString()
            }
            occurrences[baseId] = occurrence
            usedIds += candidateId
            if (candidateId == baseId) observation else observation.copy(observationId = candidateId)
        }
    }
}

class ObservationRepository(
    context: Context,
    private val codec: ObservationCodec = ObservationCodec(),
) {
    private val storeFile = AtomicFile(File(context.filesDir, FILE_NAME))
    private val photosDirectory = File(context.filesDir, PHOTOS_DIRECTORY_NAME)
    private val repositoryStartedAt = System.currentTimeMillis()
    private val mutex = Mutex()

    suspend fun load(protectedPhotoUris: Collection<String> = emptyList()): LoadResult = withContext(Dispatchers.IO) {
        mutex.withLock {
            val baseFile = storeFile.baseFile
            if (!baseFile.exists()) {
                cleanupOrphanedPhotos(emptyList(), protectedPhotoUris)
                return@withLock LoadResult(emptyList())
            }
            val result = codec.decode(storeFile.openRead().bufferedReader(Charsets.UTF_8).use { it.readText() })
            val loaded = if (result.migratedLegacyData) {
                val backedUp = preserveLegacyFileIfNeeded(result)
                write(result.observations)
                result.copy(legacyBackupCreated = backedUp)
            } else {
                result
            }
            cleanupOrphanedPhotos(loaded.observations, protectedPhotoUris)
            loaded
        }
    }

    suspend fun save(observation: ShadeObservation): List<ShadeObservation> = withContext(Dispatchers.IO) {
        mutex.withLock {
            val existing = if (storeFile.baseFile.exists()) {
                val result = codec.decode(storeFile.openRead().bufferedReader(Charsets.UTF_8).use { it.readText() })
                if (result.migratedLegacyData) {
                    preserveLegacyFileIfNeeded(result)
                    write(result.observations)
                }
                result.observations
            } else {
                emptyList()
            }
            val updated = (listOf(observation) + existing)
                .distinctBy(ShadeObservation::observationId)
                .sortedByDescending { Instant.parse(it.capturedAt) }
            write(updated)
            cleanupOrphanedPhotos(updated)
            updated
        }
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

    private fun preserveLegacyFileIfNeeded(result: LoadResult): Boolean {
        if (result.skippedLegacyRecords == 0) return false
        val backup = File(storeFile.baseFile.parentFile, LEGACY_BACKUP_FILE_NAME)
        if (!backup.exists()) storeFile.baseFile.copyTo(backup, overwrite = false)
        return true
    }

    private fun cleanupOrphanedPhotos(
        observations: List<ShadeObservation>,
        protectedPhotoUris: Collection<String> = emptyList(),
    ) {
        if (!photosDirectory.isDirectory) return
        val legacyBackup = File(storeFile.baseFile.parentFile, LEGACY_BACKUP_FILE_NAME)
        val backupPhotoUris = if (legacyBackup.isFile) {
            runCatching { codec.legacyPhotoUris(legacyBackup.readText(Charsets.UTF_8)) }.getOrDefault(emptySet())
        } else {
            emptySet()
        }
        val referencedNames = (
            observations.map(ShadeObservation::photoUri) + protectedPhotoUris + backupPhotoUris
        ).mapNotNull { photoUri ->
            runCatching { Uri.parse(photoUri).lastPathSegment }.getOrNull()
        }.toSet()
        photosDirectory.listFiles()?.forEach { photo ->
            if (photo.isFile && photo.lastModified() < repositoryStartedAt && photo.name !in referencedNames) {
                photo.delete()
            }
        }
    }

    private companion object {
        const val FILE_NAME = "shade_gis_observations.json"
        const val LEGACY_BACKUP_FILE_NAME = "shade_gis_observations.legacy-backup.jsonl"
        const val PHOTOS_DIRECTORY_NAME = "photos"
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
