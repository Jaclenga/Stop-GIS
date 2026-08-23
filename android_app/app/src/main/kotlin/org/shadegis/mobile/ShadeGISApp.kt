package org.shadegis.mobile

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.MediaStore
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import coil.compose.AsyncImage
import kotlinx.coroutines.launch
import java.io.File
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.UUID

private val lightColors = lightColorScheme(
    primary = Color(0xFF356A1F),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFB7F397),
    onPrimaryContainer = Color(0xFF082100),
)

private val darkColors = darkColorScheme(
    primary = Color(0xFF9CD67E),
    onPrimary = Color(0xFF123800),
    primaryContainer = Color(0xFF245107),
    onPrimaryContainer = Color(0xFFB7F397),
)

private data class PhotoTarget(val uri: Uri, val path: String)

@Composable
fun ShadeGISApp() {
    MaterialTheme(colorScheme = if (isSystemInDarkTheme()) darkColors else lightColors) {
        ObservationScreen()
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun ObservationScreen() {
    val context = LocalContext.current
    val repository = remember(context) { ObservationRepository(context.applicationContext) }
    val stops = remember { sampleStops() }
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }

    var selectedStopId by rememberSaveable { mutableStateOf(stops.first().stopId) }
    var selectedPhotoUri by rememberSaveable { mutableStateOf<String?>(null) }
    var selectedPhotoPath by rememberSaveable { mutableStateOf<String?>(null) }
    var pendingPhotoUri by rememberSaveable { mutableStateOf<String?>(null) }
    var pendingPhotoPath by rememberSaveable { mutableStateOf<String?>(null) }
    var coverageName by rememberSaveable { mutableStateOf<String?>(null) }
    var sourceNames by rememberSaveable { mutableStateOf(emptyList<String>()) }
    var notes by rememberSaveable { mutableStateOf("") }
    var observations by remember { mutableStateOf(emptyList<ShadeObservation>()) }
    var saving by remember { mutableStateOf(false) }

    val takePicture = rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { success ->
        val capturedUri = pendingPhotoUri
        if (success && capturedUri != null) {
            selectedPhotoPath?.let(::deleteQuietly)
            selectedPhotoUri = capturedUri
            selectedPhotoPath = pendingPhotoPath
        } else {
            pendingPhotoPath?.let(::deleteQuietly)
            scope.launch { snackbar.showSnackbar("Photo was not captured.") }
        }
        pendingPhotoUri = null
        pendingPhotoPath = null
    }
    val cameraPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val uri = pendingPhotoUri?.let(Uri::parse)
        if (granted && uri != null) {
            takePicture.launch(uri)
        } else {
            pendingPhotoPath?.let(::deleteQuietly)
            pendingPhotoUri = null
            pendingPhotoPath = null
            scope.launch { snackbar.showSnackbar("Camera permission is needed to add an observation photo.") }
        }
    }

    fun startCamera() {
        if (Intent(MediaStore.ACTION_IMAGE_CAPTURE).resolveActivity(context.packageManager) == null) {
            scope.launch { snackbar.showSnackbar("No camera app is available on this device.") }
            return
        }
        val target = runCatching { createPhotoTarget(context) }.getOrElse {
            scope.launch { snackbar.showSnackbar("A photo file could not be created.") }
            return
        }
        pendingPhotoUri = target.uri.toString()
        pendingPhotoPath = target.path
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            takePicture.launch(target.uri)
        } else {
            cameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    LaunchedEffect(repository) {
        try {
            val result = repository.load(
                protectedPhotoUris = listOfNotNull(
                    selectedPhotoUri,
                    pendingPhotoUri,
                ),
            )
            observations = result.observations
            if (result.migratedLegacyData) {
                val suffix = if (result.skippedLegacyRecords == 0) "" else {
                    " ${result.skippedLegacyRecords} unreadable record(s) were preserved in a legacy backup."
                }
                snackbar.showSnackbar("Saved observations were upgraded.$suffix")
            }
        } catch (_: Exception) {
            snackbar.showSnackbar("Saved observations could not be read. The file was left unchanged.")
        }
    }

    val selectedStop = stops.first { it.stopId == selectedStopId }
    val coverage = coverageName?.let { name -> ShadeCoverage.entries.find { it.name == name } }
    val sources = sourceNames.mapNotNull { name -> ShadeSource.entries.find { it.name == name } }
    val formIssue = when {
        selectedPhotoUri == null -> "Take a photo to continue"
        coverage == null -> "Choose the shade coverage"
        else -> null
    }

    Scaffold(
        topBar = { TopAppBar(title = { Text("Field observation") }) },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { scaffoldPadding ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(scaffoldPadding),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(20.dp),
        ) {
            item {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("Shade-GIS", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
                    Text(
                        "Document the shade that reaches the passenger waiting area.",
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            item {
                StopPicker(stops, selectedStop, onSelected = { stop ->
                    if (stop.stopId != selectedStopId) {
                        val hadDraft = selectedPhotoUri != null || coverageName != null || notes.isNotBlank()
                        selectedPhotoPath?.let(::deleteQuietly)
                        selectedStopId = stop.stopId
                        selectedPhotoUri = null
                        selectedPhotoPath = null
                        coverageName = null
                        sourceNames = emptyList()
                        notes = ""
                        if (hadDraft) {
                            scope.launch { snackbar.showSnackbar("Draft cleared after changing the bus stop.") }
                        }
                    }
                })
            }

            item {
                FormSection("1. Add a photo") {
                    selectedPhotoUri?.let { uri ->
                        AsyncImage(
                            model = uri,
                            contentDescription = "Photo of ${selectedStop.stopName}",
                            modifier = Modifier.fillMaxWidth().height(220.dp).clip(RoundedCornerShape(12.dp))
                                .background(MaterialTheme.colorScheme.surfaceVariant),
                            contentScale = ContentScale.Crop,
                        )
                    }
                    if (selectedPhotoUri == null) {
                        Text("Photograph the area where passengers wait.", color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Button(onClick = ::startCamera) { Text("Take photo") }
                    } else {
                        OutlinedButton(onClick = ::startCamera) { Text("Retake photo") }
                    }
                }
            }

            item {
                FormSection("2. Choose shade coverage") {
                    ShadeCoverage.entries.forEach { option ->
                        ChoiceRow(
                            label = option.label,
                            selected = coverage == option,
                            onClick = {
                                coverageName = option.name
                                if (option == ShadeCoverage.NO_SHADE) sourceNames = emptyList()
                            },
                        )
                    }
                }
            }

            if (coverage != null && coverage != ShadeCoverage.NO_SHADE) {
                item {
                    FormSection("3. Select shade sources") {
                        Text(
                            "Select every source that visibly shades the waiting area. You may leave this blank if uncertain.",
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            ShadeSource.entries.forEach { source ->
                                val selected = source.name in sourceNames
                                FilterChip(
                                    selected = selected,
                                    onClick = {
                                        sourceNames = if (selected) sourceNames - source.name else sourceNames + source.name
                                    },
                                    label = { Text(source.label) },
                                )
                            }
                        }
                    }
                }
            }

            item {
                FormSection(if (coverage == null || coverage == ShadeCoverage.NO_SHADE) "3. Add notes" else "4. Add notes") {
                    OutlinedTextField(
                        value = notes,
                        onValueChange = { notes = it.take(ShadeObservation.MAX_NOTES_LENGTH) },
                        label = { Text("Notes (optional)") },
                        supportingText = { Text("${notes.length}/${ShadeObservation.MAX_NOTES_LENGTH}") },
                        minLines = 3,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }

            item {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(
                        enabled = formIssue == null && !saving,
                        modifier = Modifier.fillMaxWidth(),
                        onClick = {
                            val validCoverage = coverage ?: return@Button
                            val validUri = selectedPhotoUri ?: return@Button
                            val observation = ShadeObservation.create(
                                stop = selectedStop,
                                photoUri = validUri,
                                coverage = validCoverage,
                                sources = sources,
                                notes = notes,
                            )
                            saving = true
                            scope.launch {
                                try {
                                    observations = repository.save(observation)
                                    selectedPhotoUri = null
                                    selectedPhotoPath = null
                                    coverageName = null
                                    sourceNames = emptyList()
                                    notes = ""
                                    snackbar.showSnackbar("Observation saved for ${selectedStop.stopName}.")
                                } catch (_: Exception) {
                                    snackbar.showSnackbar("The observation could not be saved. Please try again.")
                                } finally {
                                    saving = false
                                }
                            }
                        },
                    ) { Text(if (saving) "Saving…" else "Save observation") }
                    formIssue?.let { Text(it, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
            }

            if (observations.isNotEmpty()) {
                item {
                    Text(
                        "Saved observations (${observations.size})",
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                    )
                }
                items(observations, key = ShadeObservation::observationId) { observation ->
                    ObservationCard(observation)
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun StopPicker(stops: List<Stop>, selected: Stop, onSelected: (Stop) -> Unit) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Bus stop", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = !expanded }) {
            OutlinedTextField(
                value = selected.stopName,
                onValueChange = {},
                readOnly = true,
                label = { Text("Selected stop") },
                supportingText = { Text("${selected.stopId} • Routes ${selected.routeLabels.joinToString(", ")}") },
                trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded) },
                modifier = Modifier.menuAnchor().fillMaxWidth(),
            )
            ExposedDropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
                stops.forEach { stop ->
                    DropdownMenuItem(
                        text = {
                            Column {
                                Text(stop.stopName)
                                Text(
                                    "${stop.stopId} • Routes ${stop.routeLabels.joinToString(", ")}",
                                    style = MaterialTheme.typography.bodySmall,
                                )
                            }
                        },
                        onClick = { onSelected(stop); expanded = false },
                    )
                }
            }
        }
    }
}

@Composable
private fun FormSection(title: String, content: @Composable () -> Unit) {
    Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            content()
        }
    }
}

@Composable
private fun ChoiceRow(label: String, selected: Boolean, onClick: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().background(
            if (selected) MaterialTheme.colorScheme.primaryContainer else Color.Transparent,
            RoundedCornerShape(10.dp),
        ).selectable(selected = selected, onClick = onClick, role = Role.RadioButton)
            .padding(horizontal = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = null)
        Text(label, modifier = Modifier.padding(vertical = 12.dp))
    }
}

@Composable
private fun ObservationCard(observation: ShadeObservation) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(observation.stopName, fontWeight = FontWeight.Bold)
            Text(observation.shadeCoverage.label)
            if (observation.shadeSources.isNotEmpty()) {
                Text(observation.shadeSources.joinToString(", ") { it.label })
            }
            Text(formatTimestamp(observation.capturedAt), color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

private fun createPhotoTarget(context: Context): PhotoTarget {
    val directory = File(context.filesDir, "photos").apply { check(exists() || mkdirs()) }
    val file = File(directory, "observation_${UUID.randomUUID()}.jpg").apply { createNewFile() }
    val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
    return PhotoTarget(uri, file.absolutePath)
}

private fun deleteQuietly(path: String) {
    runCatching { File(path).delete() }
}

private fun formatTimestamp(value: String): String = runCatching {
    DateTimeFormatter.ofPattern("MMM d, yyyy • h:mm a")
        .withZone(ZoneId.systemDefault())
        .format(Instant.parse(value))
}.getOrDefault(value)
