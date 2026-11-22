package app.gymgarmin.sync

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.ActiveCaloriesBurnedRecord
import androidx.health.connect.client.records.DistanceRecord
import androidx.health.connect.client.records.ExerciseSegment
import androidx.health.connect.client.records.ExerciseSessionRecord
import androidx.health.connect.client.records.HeartRateRecord
import androidx.health.connect.client.records.HeartRateVariabilityRmssdRecord
import androidx.health.connect.client.records.Record
import androidx.health.connect.client.records.RestingHeartRateRecord
import androidx.health.connect.client.records.SleepSessionRecord
import androidx.health.connect.client.records.StepsRecord
import androidx.health.connect.client.records.TotalCaloriesBurnedRecord
import androidx.health.connect.client.records.Vo2MaxRecord
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.request.AggregateGroupByPeriodRequest
import androidx.health.connect.client.request.AggregateRequest
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import org.json.JSONArray
import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.time.LocalDate
import java.time.Period
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import kotlin.reflect.KClass

/** Lê o Health Connect e monta o JSON que o HAX recebe em /api/ingest. */
class HealthReader(context: Context) {
    private val client = HealthConnectClient.getOrCreate(context)
    private val zone: ZoneId = ZoneId.systemDefault()

    companion object {
        val PERMISSIONS: Set<String> = setOf(
            HealthPermission.getReadPermission(ExerciseSessionRecord::class),
            HealthPermission.getReadPermission(SleepSessionRecord::class),
            HealthPermission.getReadPermission(HeartRateRecord::class),
            HealthPermission.getReadPermission(RestingHeartRateRecord::class),
            HealthPermission.getReadPermission(HeartRateVariabilityRmssdRecord::class),
            HealthPermission.getReadPermission(StepsRecord::class),
            HealthPermission.getReadPermission(DistanceRecord::class),
            HealthPermission.getReadPermission(TotalCaloriesBurnedRecord::class),
            HealthPermission.getReadPermission(ActiveCaloriesBurnedRecord::class),
            HealthPermission.getReadPermission(Vo2MaxRecord::class),
            HealthPermission.getReadPermission(WeightRecord::class),
            HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND,
        )

        fun typeName(t: Int): String? = when (t) {
            ExerciseSessionRecord.EXERCISE_TYPE_RUNNING -> "RUNNING"
            ExerciseSessionRecord.EXERCISE_TYPE_RUNNING_TREADMILL -> "RUNNING_TREADMILL"
            ExerciseSessionRecord.EXERCISE_TYPE_BIKING -> "BIKING"
            ExerciseSessionRecord.EXERCISE_TYPE_BIKING_STATIONARY -> "BIKING_STATIONARY"
            ExerciseSessionRecord.EXERCISE_TYPE_WALKING -> "WALKING"
            ExerciseSessionRecord.EXERCISE_TYPE_HIKING -> "HIKING"
            ExerciseSessionRecord.EXERCISE_TYPE_SWIMMING_POOL -> "SWIMMING_POOL"
            ExerciseSessionRecord.EXERCISE_TYPE_SWIMMING_OPEN_WATER -> "SWIMMING_OPEN_WATER"
            ExerciseSessionRecord.EXERCISE_TYPE_ROWING_MACHINE -> "ROWING_MACHINE"
            ExerciseSessionRecord.EXERCISE_TYPE_ROWING -> "ROWING"
            ExerciseSessionRecord.EXERCISE_TYPE_ELLIPTICAL -> "ELLIPTICAL"
            ExerciseSessionRecord.EXERCISE_TYPE_STAIR_CLIMBING,
            ExerciseSessionRecord.EXERCISE_TYPE_STAIR_CLIMBING_MACHINE -> "STAIR_CLIMBING"
            ExerciseSessionRecord.EXERCISE_TYPE_STRENGTH_TRAINING,
            ExerciseSessionRecord.EXERCISE_TYPE_WEIGHTLIFTING,
            ExerciseSessionRecord.EXERCISE_TYPE_CALISTHENICS -> "STRENGTH_TRAINING"
            ExerciseSessionRecord.EXERCISE_TYPE_HIGH_INTENSITY_INTERVAL_TRAINING -> "HIIT"  // aula de HIIT não é a musculação do dia
            // os demais entram no HAX como atividade genérica (tempo, FC e calorias): nada é descartado aqui.
            // Relógios como Amazfit/Zepp e Xiaomi costumam gravar a musculação como "outro exercício".
            ExerciseSessionRecord.EXERCISE_TYPE_YOGA -> "YOGA"
            ExerciseSessionRecord.EXERCISE_TYPE_PILATES -> "PILATES"
            ExerciseSessionRecord.EXERCISE_TYPE_STRETCHING -> "STRETCHING"
            ExerciseSessionRecord.EXERCISE_TYPE_DANCING -> "DANCING"
            ExerciseSessionRecord.EXERCISE_TYPE_SOCCER -> "SOCCER"
            ExerciseSessionRecord.EXERCISE_TYPE_BASKETBALL -> "BASKETBALL"
            ExerciseSessionRecord.EXERCISE_TYPE_VOLLEYBALL -> "VOLLEYBALL"
            ExerciseSessionRecord.EXERCISE_TYPE_TENNIS -> "TENNIS"
            ExerciseSessionRecord.EXERCISE_TYPE_TABLE_TENNIS -> "TABLE_TENNIS"
            ExerciseSessionRecord.EXERCISE_TYPE_BADMINTON -> "BADMINTON"
            ExerciseSessionRecord.EXERCISE_TYPE_SQUASH -> "SQUASH"
            ExerciseSessionRecord.EXERCISE_TYPE_BOXING -> "BOXING"
            ExerciseSessionRecord.EXERCISE_TYPE_MARTIAL_ARTS -> "MARTIAL_ARTS"
            ExerciseSessionRecord.EXERCISE_TYPE_ROCK_CLIMBING -> "ROCK_CLIMBING"
            ExerciseSessionRecord.EXERCISE_TYPE_SURFING -> "SURFING"
            ExerciseSessionRecord.EXERCISE_TYPE_PADDLING -> "PADDLING"
            ExerciseSessionRecord.EXERCISE_TYPE_SKIING -> "SKIING"
            ExerciseSessionRecord.EXERCISE_TYPE_SNOWBOARDING -> "SNOWBOARDING"
            ExerciseSessionRecord.EXERCISE_TYPE_SKATING -> "SKATING"
            ExerciseSessionRecord.EXERCISE_TYPE_GOLF -> "GOLF"
            ExerciseSessionRecord.EXERCISE_TYPE_BOOT_CAMP -> "BOOT_CAMP"
            ExerciseSessionRecord.EXERCISE_TYPE_EXERCISE_CLASS -> "EXERCISE_CLASS"
            ExerciseSessionRecord.EXERCISE_TYPE_GUIDED_BREATHING -> "GUIDED_BREATHING"
            ExerciseSessionRecord.EXERCISE_TYPE_OTHER_WORKOUT -> "OTHER_WORKOUT"
            else -> "OTHER_WORKOUT_$t"
        }

        const val APP_VERSION = "1.3.2"
    }

    /** Tipo do segmento (série) do Health Connect → nome que o HAX entende. */
    private fun segmentName(t: Int): String? = when (t) {
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_ARM_CURL -> "ARM_CURL"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_BACK_EXTENSION -> "BACK_EXTENSION"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_BARBELL_SHOULDER_PRESS -> "BARBELL_SHOULDER_PRESS"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_BENCH_PRESS -> "BENCH_PRESS"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_BENCH_SIT_UP -> "BENCH_SIT_UP"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_CRUNCH -> "CRUNCH"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_DEADLIFT -> "DEADLIFT"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_DOUBLE_ARM_TRICEPS_EXTENSION -> "DOUBLE_ARM_TRICEPS_EXTENSION"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_DUMBBELL_CURL_LEFT_ARM -> "DUMBBELL_CURL_LEFT_ARM"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_DUMBBELL_CURL_RIGHT_ARM -> "DUMBBELL_CURL_RIGHT_ARM"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_DUMBBELL_FRONT_RAISE -> "DUMBBELL_FRONT_RAISE"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_DUMBBELL_LATERAL_RAISE -> "DUMBBELL_LATERAL_RAISE"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_DUMBBELL_ROW -> "DUMBBELL_ROW"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_DUMBBELL_TRICEPS_EXTENSION_LEFT_ARM -> "DUMBBELL_TRICEPS_EXTENSION_LEFT_ARM"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_DUMBBELL_TRICEPS_EXTENSION_RIGHT_ARM -> "DUMBBELL_TRICEPS_EXTENSION_RIGHT_ARM"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_DUMBBELL_TRICEPS_EXTENSION_TWO_ARM -> "DUMBBELL_TRICEPS_EXTENSION_TWO_ARM"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_FRONT_RAISE -> "FRONT_RAISE"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_HIP_THRUST -> "HIP_THRUST"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_KETTLEBELL_SWING -> "KETTLEBELL_SWING"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_LATERAL_RAISE -> "LATERAL_RAISE"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_LAT_PULL_DOWN -> "LAT_PULL_DOWN"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_LEG_CURL -> "LEG_CURL"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_LEG_EXTENSION -> "LEG_EXTENSION"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_LEG_PRESS -> "LEG_PRESS"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_LEG_RAISE -> "LEG_RAISE"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_LUNGE -> "LUNGE"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_PLANK -> "PLANK"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_PULL_UP -> "PULL_UP"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_SHOULDER_PRESS -> "SHOULDER_PRESS"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_SINGLE_ARM_TRICEPS_EXTENSION -> "SINGLE_ARM_TRICEPS_EXTENSION"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_SIT_UP -> "SIT_UP"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_SQUAT -> "SQUAT"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_WEIGHTLIFTING -> "WEIGHTLIFTING"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_OTHER_WORKOUT -> "OTHER_WORKOUT"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_UNKNOWN -> "UNKNOWN"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_REST -> "REST"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_PAUSE -> "PAUSE"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_RUNNING -> "RUNNING"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_RUNNING_TREADMILL -> "RUNNING_TREADMILL"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_BIKING -> "BIKING"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_BIKING_STATIONARY -> "BIKING_STATIONARY"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_ELLIPTICAL -> "ELLIPTICAL"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_ROWING_MACHINE -> "ROWING_MACHINE"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_JUMP_ROPE -> "JUMP_ROPE"
            ExerciseSegment.EXERCISE_SEGMENT_TYPE_BURPEE -> "BURPEE"
            else -> null
        }

    suspend fun grantedAll(): Boolean =
        client.permissionController.getGrantedPermissions().containsAll(PERMISSIONS - HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND)

    private suspend fun <T : Record> readAll(type: KClass<T>, from: Instant, to: Instant): List<T> {
        val out = mutableListOf<T>()
        var token: String? = null
        do {
            val resp = client.readRecords(ReadRecordsRequest(type, TimeRangeFilter.between(from, to), pageToken = token))
            out += resp.records
            token = resp.pageToken
        } while (token != null && out.size < 5000)
        return out
    }

    private inline fun safe(block: () -> Unit) {
        try { block() } catch (_: Exception) { /* sem permissão ou sem dado: segue com o resto */ }
    }

    suspend fun build(days: Long): JSONObject {
        val now = Instant.now()
        val from = LocalDate.now(zone).minusDays(days).atStartOfDay(zone).toInstant()
        val byDay = sortedMapOf<String, JSONObject>()
        fun day(d: LocalDate) = byDay.getOrPut(d.toString()) { JSONObject().put("date", d.toString()) }

        safe {  // sono: a noite conta para o dia em que termina
            for (s in readAll(SleepSessionRecord::class, from.minus(Duration.ofHours(14)), now)) {
                val endDay = s.endTime.atZone(zone).toLocalDate()
                val stages = mutableMapOf<Int, Long>()
                for (st in s.stages) stages[st.stage] = (stages[st.stage] ?: 0L) + Duration.between(st.startTime, st.endTime).toMinutes()
                val awake = (stages[SleepSessionRecord.STAGE_TYPE_AWAKE] ?: 0) + (stages[SleepSessionRecord.STAGE_TYPE_AWAKE_IN_BED] ?: 0) +
                    (stages[SleepSessionRecord.STAGE_TYPE_OUT_OF_BED] ?: 0)
                val total = Duration.between(s.startTime, s.endTime).toMinutes()
                val asleep = if (stages.isNotEmpty()) total - awake else total
                if (asleep < 120) continue // cochilo
                val o = day(endDay)
                o.put("sleep_h", Math.round(asleep / 60.0 * 100) / 100.0)
                stages[SleepSessionRecord.STAGE_TYPE_DEEP]?.let { o.put("deep_min", it) }
                stages[SleepSessionRecord.STAGE_TYPE_REM]?.let { o.put("rem_min", it) }
                stages[SleepSessionRecord.STAGE_TYPE_LIGHT]?.let { o.put("light_min", it) }
                if (stages.isNotEmpty()) o.put("awake_min", awake)
                val hm = DateTimeFormatter.ofPattern("HH:mm")
                o.put("sleep_start", s.startTime.atZone(zone).format(hm))
                o.put("sleep_end", s.endTime.atZone(zone).format(hm))
            }
        }
        safe {
            val g = readAll(RestingHeartRateRecord::class, from, now).groupBy { it.time.atZone(zone).toLocalDate() }
            for ((d, rs) in g) day(d).put("rhr", rs.map { it.beatsPerMinute }.average().toInt())
        }
        safe {  // VFC da noite (RMSSD)
            val g = readAll(HeartRateVariabilityRmssdRecord::class, from.minus(Duration.ofHours(14)), now)
                .groupBy { it.time.plus(Duration.ofHours(8)).atZone(zone).toLocalDate() }
            for ((d, rs) in g) day(d).put("hrv", Math.round(rs.map { it.heartRateVariabilityMillis }.average() * 10) / 10.0).put("hrv_kind", "rmssd")
        }
        safe {
            val res = client.aggregateGroupByPeriod(AggregateGroupByPeriodRequest(
                setOf(StepsRecord.COUNT_TOTAL),
                TimeRangeFilter.between(from.atZone(zone).toLocalDateTime(), now.atZone(zone).toLocalDateTime()),
                Period.ofDays(1)))
            for (r in res) r.result[StepsRecord.COUNT_TOTAL]?.let { day(r.startTime.toLocalDate()).put("steps", it) }
        }
        safe {
            readAll(Vo2MaxRecord::class, from, now).maxByOrNull { it.time }?.let {
                day(it.time.atZone(zone).toLocalDate()).put("vo2max", it.vo2MillilitersPerMinuteKilogram)
            }
        }
        safe {
            readAll(WeightRecord::class, from.minus(Duration.ofDays(30)), now).maxByOrNull { it.time }?.let {
                day(it.time.atZone(zone).toLocalDate()).put("weight_kg", Math.round(it.weight.inKilograms * 10) / 10.0)
            }
        }

        // diagnóstico: o que o Health Connect tinha para ler (o HAX mostra em Perfil → Relógios)
        // treinos: pelo menos 7 dias, porque o app do relógio (Zepp, Mi Fitness) às vezes entrega com dias de atraso
        val workoutDays = maxOf(days, 7L)
        val fromWorkouts = LocalDate.now(zone).minusDays(workoutDays).atStartOfDay(zone).toInstant()
        val diag = JSONObject().put("app", APP_VERSION).put("window_days", days).put("workout_days", workoutDays)
        safe {
            val granted = client.permissionController.getGrantedPermissions()
            val missing = JSONArray()
            for (p in PERMISSIONS) if (p !in granted) missing.put(p.substringAfterLast('.'))
            diag.put("missing", missing)
        }
        diag.put("health_days", byDay.size)
        diag.put("sleep_days", byDay.values.count { it.has("sleep_h") })
        diag.put("steps_days", byDay.values.count { it.has("steps") })
        val typeCount = sortedMapOf<String, Int>()
        val origins = sortedSetOf<String>()

        val workouts = JSONArray()
        var segTotal = 0
        safe {
            for (s in readAll(ExerciseSessionRecord::class, fromWorkouts, now)) {
                val type = typeName(s.exerciseType) ?: "OTHER_WORKOUT"
                typeCount[type] = (typeCount[type] ?: 0) + 1
                origins.add(s.metadata.dataOrigin.packageName)
                val w = JSONObject()
                    .put("id", s.metadata.id)
                    .put("type", type)
                    .put("name", s.title?.toString()?.takeIf { it.isNotBlank() } ?: "")  // sem título do relógio: o HAX dá o nome em português
                    .put("start", s.startTime.atOffset(s.startZoneOffset ?: ZoneOffset.UTC).toString())
                    .put("end", s.endTime.atOffset(s.endZoneOffset ?: ZoneOffset.UTC).toString())
                    .put("duration_min", Duration.between(s.startTime, s.endTime).seconds / 60.0)
                // séries que o relógio gravou (Samsung, Pixel Watch e outros): um segmento por série, com repetições
                if (s.segments.isNotEmpty()) {
                    val segs = JSONArray()
                    for (sg in s.segments.take(300)) {
                        val name = segmentName(sg.segmentType) ?: continue
                        segs.put(JSONObject().put("type", name).put("reps", sg.repetitions)
                            .put("start", sg.startTime.atOffset(s.startZoneOffset ?: ZoneOffset.UTC).toLocalDateTime().toString())
                            .put("end", sg.endTime.atOffset(s.startZoneOffset ?: ZoneOffset.UTC).toLocalDateTime().toString()))
                    }
                    if (segs.length() > 0) { w.put("segments", segs); segTotal += segs.length() }
                }
                val range = TimeRangeFilter.between(s.startTime, s.endTime)
                safe {
                    val a = client.aggregate(AggregateRequest(setOf(HeartRateRecord.BPM_AVG, HeartRateRecord.BPM_MAX), range))
                    a[HeartRateRecord.BPM_AVG]?.let { w.put("avg_hr", it) }
                    a[HeartRateRecord.BPM_MAX]?.let { w.put("max_hr", it) }
                }
                safe {
                    client.aggregate(AggregateRequest(setOf(DistanceRecord.DISTANCE_TOTAL), range))[DistanceRecord.DISTANCE_TOTAL]
                        ?.let { if (it.inMeters > 0) w.put("distance_km", Math.round(it.inKilometers * 1000) / 1000.0) }
                }
                safe {
                    val a = client.aggregate(AggregateRequest(setOf(TotalCaloriesBurnedRecord.ENERGY_TOTAL, ActiveCaloriesBurnedRecord.ACTIVE_CALORIES_TOTAL), range))
                    (a[ActiveCaloriesBurnedRecord.ACTIVE_CALORIES_TOTAL] ?: a[TotalCaloriesBurnedRecord.ENERGY_TOTAL])
                        ?.let { if (it.inKilocalories > 0) w.put("kcal", Math.round(it.inKilocalories)) }
                }
                workouts.put(w)
            }
        }
        diag.put("exercise_total", workouts.length())
        diag.put("segments_total", segTotal)
        diag.put("exercise_types", JSONObject(typeCount as Map<*, *>))
        diag.put("exercise_apps", JSONArray(origins.toList()))
        return JSONObject()
            .put("source", "android")
            .put("days", JSONArray(byDay.values.toList()))
            .put("workouts", workouts)
            .put("diag", diag)
    }
}
