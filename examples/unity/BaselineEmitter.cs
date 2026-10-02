// Dedicated-server helper. Do not run this on a client, and do not let the
// client POST the line. JsonUtility cannot express this object cleanly;
// the line is small enough to format by hand.
using System.Globalization;
using System.Text;

public static class BaselineEmitter
{
    public static string Shot(
        string gameId,
        string matchId,
        string playerId,
        long tMs,
        string skillBand,
        string weaponClass,
        string weaponId,
        bool hit,
        string hitbox,
        double distanceM,
        double? expectedMaxSpeed,
        bool onGround,
        string cause)
    {
        var culture = CultureInfo.InvariantCulture;
        var builder = new StringBuilder();
        builder.Append("{\"game_id\":\"").Append(Esc(gameId));
        builder.Append("\",\"match_id\":\"").Append(Esc(matchId));
        builder.Append("\",\"player_id\":\"").Append(Esc(playerId));
        builder.Append("\",\"t_ms\":").Append(tMs.ToString(culture));
        builder.Append(",\"event_type\":\"shot\",\"skill_band\":\"").Append(Esc(skillBand));
        builder.Append("\",\"weapon_class\":\"").Append(Esc(weaponClass));
        builder.Append("\",\"weapon_id\":\"").Append(Esc(weaponId));
        builder.Append("\",\"hit\":").Append(hit ? "true" : "false");
        if (!string.IsNullOrEmpty(hitbox))
        {
            builder.Append(",\"hitbox\":\"").Append(Esc(hitbox)).Append('"');
        }
        builder.Append(",\"distance_m\":").Append(distanceM.ToString("0.###", culture));
        if (expectedMaxSpeed.HasValue)
        {
            builder.Append(",\"expected_max_ground_speed_mps\":")
                .Append(expectedMaxSpeed.Value.ToString("0.###", culture));
        }
        builder.Append(",\"on_ground\":").Append(onGround ? "true" : "false");
        builder.Append(",\"displacement_cause\":\"").Append(Esc(cause)).Append("\"}");
        return builder.ToString();
    }

    static string Esc(string value)
    {
        return (value ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"");
    }
}
