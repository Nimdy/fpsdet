// fpsdet event emitter for a Unity dedicated server.
//
// Writes one JSON object per line (NDJSON) for every shot and every movement
// sample the server accepts. Field names match schema/combat_event.schema.json.
// Feed the file to `fpsdet ingest`, or ship it to your collector.
//
// Server only. The #if below compiles this file into dedicated server builds
// (UNITY_SERVER, set by the Dedicated Server build target) and into the editor
// (UNITY_EDITOR, so you can try it in Play Mode). Player builds do not contain
// it, so no client can write its own line. Wrap your calls in the same #if, or
// player builds will not compile. Call it from server-authoritative code, after
// the server has decided the hit with its own trace.
//
// Usage:
//   BaselineEmitter.BeginMatch("your-game", "m-1001");          // main thread, at match start
//   BaselineEmitter.Shot(pseudonym, BaselineEmitter.MatchTimeMs, "average", "rifle", hit: true,
//       weaponId: "ak-74", hitbox: "head", distanceM: 42.5);
//   BaselineEmitter.Movement(pseudonym, BaselineEmitter.MatchTimeMs, "average",
//       speedMps: 5.4, onGround: true, displacementCause: DisplacementCause.None,
//       loadoutWeightKg: 14.2, expectedMaxGroundSpeedMps: 6.1);
//
// t_ms is milliseconds since match start on the server clock, not wall time.
// MatchTimeMs is one source; your own server tick time works too.
// game_id and match_id are set once by BeginMatch: one match per server process.
//
// Every optional argument is nullable. Null is left out of the line. So is
// NaN or Infinity: a number that is not finite is omitted, never written.
// Strings are fully JSON-escaped. Numbers use the invariant culture, so a
// server running in a "1,5" locale still writes 1.5.
//
// Other schema fields (map_id, through_geometry, private_track_ms,
// wire_error_deg, picture_error_deg, interp_delay_ms, acquire_ms) follow the
// same pattern: add a nullable parameter and one Str/Num/Bool call.

#if UNITY_SERVER || UNITY_EDITOR
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEngine;

namespace Fpsdet
{
    /// <summary>Where finished lines go. Set BaselineEmitter.Sink to use your own (a socket, a log shipper).</summary>
    public interface IEventSink : IDisposable
    {
        /// <summary>Write one complete JSON object. The sink adds the newline.</summary>
        void WriteLine(string json);
    }

    /// <summary>Appends lines to a UTF-8 file. AutoFlush, so a crash loses at most the line being written.</summary>
    public sealed class FileSink : IEventSink
    {
        readonly StreamWriter writer;

        public FileSink(string path)
        {
            FilePath = path;
            string folder = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(folder))
            {
                Directory.CreateDirectory(folder);
            }
            // FileShare.Read lets a log shipper tail the file while the server writes it.
            var stream = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.Read);
            writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true };
        }

        public string FilePath { get; private set; }

        public void WriteLine(string json)
        {
            writer.Write(json);
            writer.Write('\n');
        }

        public void Dispose()
        {
            writer.Dispose();
        }
    }

    /// <summary>Values for displacement_cause. None is a normal sprint. Any other value is excluded from the speed check.</summary>
    public static class DisplacementCause
    {
        public const string None = "none";
        public const string Unknown = "unknown";
        public const string Explosion = "explosion";
        public const string Knockback = "knockback";
        public const string Ragdoll = "ragdoll";
        public const string Vehicle = "vehicle";
        public const string Parachute = "parachute";
        public const string Ladder = "ladder";
        public const string Ability = "ability";
        public const string Launch = "launch";
        public const string TeleportVolume = "teleport_volume";
        public const string Zipline = "zipline";
        public const string Admin = "admin";
    }

    /// <summary>Values for information_state: what this client could know about that enemy, by the server's own queries.</summary>
    public static class InformationState
    {
        public const string Visible = "visible";
        public const string Audio = "audio";
        /// <summary>The server's line-of-sight and audio queries both failed for this client. A wrong label manufactures a case.</summary>
        public const string Unknowable = "unknowable";
    }

    public static class BaselineEmitter
    {
        static readonly object gate = new object();
        static readonly Stopwatch matchClock = new Stopwatch();
        static IEventSink sink;
        static bool quitHooked;
        static bool warned;
        static string gameId;
        static string matchId;

        /// <summary>Also write a server wall-clock "utc" field. fpsdet ingest uses it to pick the day folder.</summary>
        public static bool WriteUtc = true;

        /// <summary>
        /// Where lines are written. Defaults to a new file under
        /// Application.persistentDataPath/fpsdet/ on the first line. Setting it disposes the previous sink.
        /// </summary>
        public static IEventSink Sink
        {
            get
            {
                lock (gate)
                {
                    return sink;
                }
            }
            set
            {
                lock (gate)
                {
                    if (sink != null && !ReferenceEquals(sink, value))
                    {
                        sink.Dispose();
                    }
                    sink = value;
                }
            }
        }

        /// <summary>Milliseconds since BeginMatch, from a monotonic clock.</summary>
        public static long MatchTimeMs
        {
            get { return matchClock.ElapsedMilliseconds; }
        }

        /// <summary>
        /// Call on the main thread when a match starts. Sets game_id and match_id for every
        /// following line, restarts the match clock, and opens the default file if no sink is set.
        /// </summary>
        public static void BeginMatch(string game, string match)
        {
            lock (gate)
            {
                gameId = game;
                matchId = match;
                matchClock.Reset();
                matchClock.Start();
                // Application.persistentDataPath has to be read on the main thread, so open here.
                if (sink == null)
                {
                    sink = DefaultSink();
                }
            }
        }

        /// <summary>Flush and close the sink. Runs automatically when the application quits.</summary>
        public static void Close()
        {
            lock (gate)
            {
                if (sink != null)
                {
                    sink.Dispose();
                    sink = null;
                }
            }
        }

        static IEventSink DefaultSink()
        {
            if (!quitHooked)
            {
                Application.quitting += Close;
                quitHooked = true;
            }
            // A new file per server process, so two processes never append to the same file.
            string name = string.Format(
                CultureInfo.InvariantCulture,
                "events-{0:yyyyMMdd-HHmmss}-{1}.ndjson",
                DateTime.UtcNow,
                Guid.NewGuid().ToString("N").Substring(0, 8));
            string path = Path.Combine(Path.Combine(Application.persistentDataPath, "fpsdet"), name);
            return new FileSink(path);
        }

        /// <summary>
        /// One line per shot the server accepted, after the server decided the hit.
        /// skillBand: developing, average, advanced, elite, or unrated.
        /// hitbox: head, upper_torso, lower_torso, or limbs. Null on a miss.
        /// modSet: null means "not sent". An empty array means "no attachments".
        /// informationState: see InformationState. Send it with enemyId and aimJitterDeg, or not at all.
        /// sincePerceivedMs: milliseconds since this client last saw or heard enemyId,
        /// by the server's own line-of-sight and audio queries.
        /// </summary>
        public static void Shot(
            string playerId,
            long tMs,
            string skillBand,
            string weaponClass,
            bool hit,
            string weaponId = null,
            string[] modSet = null,
            string hitbox = null,
            double? distanceM = null,
            int? sprayIndex = null,
            double? recoilPitchDeg = null,
            double? appliedRecoilPitchDeg = null,
            double? compensationPitchDeg = null,
            double? expectedMinRecoilPitchDeg = null,
            string informationState = null,
            string enemyId = null,
            double? aimJitterDeg = null,
            double? hiddenTrackMs = null,
            double? sincePerceivedMs = null,
            string partyId = null)
        {
            var line = new Line(playerId, tMs, "shot", skillBand);
            line.Str("party_id", partyId);
            line.Str("weapon_class", weaponClass);
            line.Str("weapon_id", weaponId);
            line.StrArray("mod_set", modSet);
            line.Bool("hit", hit);
            line.Str("hitbox", hitbox);
            line.Num("distance_m", distanceM);
            line.Int("spray_index", sprayIndex);
            line.Num("recoil_pitch_deg", recoilPitchDeg);
            line.Num("applied_recoil_pitch_deg", appliedRecoilPitchDeg);
            line.Num("compensation_pitch_deg", compensationPitchDeg);
            line.Num("expected_min_recoil_pitch_deg", expectedMinRecoilPitchDeg);
            line.Str("information_state", informationState);
            line.Str("enemy_id", enemyId);
            line.Num("aim_jitter_deg", aimJitterDeg);
            line.Num("hidden_track_ms", hiddenTrackMs);
            line.Num("since_perceived_ms", sincePerceivedMs);
            Write(line.Finish());
        }

        /// <summary>
        /// One line per movement sample. Send on a fixed cadence (10 Hz is enough), not only on shots.
        /// onGround must be true for the sample to count as ground speed.
        /// displacementCause: DisplacementCause.None for a normal sprint, otherwise why the body moved.
        /// expectedMaxGroundSpeedMps: the cap the server applied this tick, after weight, stance, and perks.
        /// It wins over the profile's weight table, so send it when you have it.
        /// </summary>
        public static void Movement(
            string playerId,
            long tMs,
            string skillBand,
            double speedMps,
            bool onGround,
            string displacementCause,
            double? loadoutWeightKg = null,
            double? expectedMaxGroundSpeedMps = null)
        {
            var line = new Line(playerId, tMs, "movement", skillBand);
            line.Num("speed_mps", speedMps);
            line.Bool("on_ground", onGround);
            line.Str("displacement_cause", displacementCause);
            line.Num("loadout_weight_kg", loadoutWeightKg);
            line.Num("expected_max_ground_speed_mps", expectedMaxGroundSpeedMps);
            Write(line.Finish());
        }

        static void Write(string json)
        {
            lock (gate)
            {
                if (sink == null || matchId == null)
                {
                    // No BeginMatch yet: no match_id, and no safe place to open the file from.
                    if (!warned)
                    {
                        UnityEngine.Debug.LogWarning("fpsdet: call BaselineEmitter.BeginMatch before emitting. Line dropped.");
                        warned = true;
                    }
                    return;
                }
                sink.WriteLine(json);
            }
        }

        /// <summary>Builds one JSON object. Null and non-finite values are left out.</summary>
        sealed class Line
        {
            static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
            readonly StringBuilder b = new StringBuilder(512);

            public Line(string playerId, long tMs, string eventType, string skillBand)
            {
                b.Append('{');
                Str("game_id", gameId);
                Str("match_id", matchId);
                Str("player_id", playerId);
                Raw("t_ms", tMs.ToString(Inv));
                if (WriteUtc)
                {
                    Str("utc", DateTime.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", Inv));
                }
                Str("event_type", eventType);
                Str("skill_band", skillBand);
            }

            public void Str(string key, string value)
            {
                if (value == null)
                {
                    return;
                }
                Key(key);
                AppendString(value);
            }

            public void StrArray(string key, string[] values)
            {
                if (values == null)
                {
                    return;
                }
                Key(key);
                b.Append('[');
                bool first = true;
                foreach (string value in values)
                {
                    if (value == null)
                    {
                        continue;
                    }
                    if (!first)
                    {
                        b.Append(',');
                    }
                    AppendString(value);
                    first = false;
                }
                b.Append(']');
            }

            public void Num(string key, double? value)
            {
                if (!value.HasValue || double.IsNaN(value.Value) || double.IsInfinity(value.Value))
                {
                    return;
                }
                // Up to six decimals, never an exponent, always "." as the separator.
                Raw(key, value.Value.ToString("0.######", Inv));
            }

            public void Int(string key, int? value)
            {
                if (value.HasValue)
                {
                    Raw(key, value.Value.ToString(Inv));
                }
            }

            public void Bool(string key, bool? value)
            {
                if (value.HasValue)
                {
                    Raw(key, value.Value ? "true" : "false");
                }
            }

            public string Finish()
            {
                b.Append('}');
                return b.ToString();
            }

            void Raw(string key, string literal)
            {
                Key(key);
                b.Append(literal);
            }

            void Key(string key)
            {
                if (b.Length > 1)
                {
                    b.Append(',');
                }
                AppendString(key);
                b.Append(':');
            }

            // JSON string: quote and backslash escaped, every control character below 0x20 as \u00XX.
            void AppendString(string value)
            {
                b.Append('"');
                foreach (char c in value)
                {
                    if (c == '"')
                    {
                        b.Append("\\\"");
                    }
                    else if (c == '\\')
                    {
                        b.Append("\\\\");
                    }
                    else if (c < 0x20)
                    {
                        b.Append("\\u00").Append(((int)c).ToString("x2", Inv));
                    }
                    else
                    {
                        b.Append(c);
                    }
                }
                b.Append('"');
            }
        }
    }
}
#endif
