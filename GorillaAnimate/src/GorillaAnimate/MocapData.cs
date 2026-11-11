using System;
using System.Collections.Generic;

namespace GorillaAnimate
{
    [Serializable]
    public sealed class BoneDef
    {
        public string name;
        public int parent;

        public BoneDef() { }

        public BoneDef(string name, int parent)
        {
            this.name = name;
            this.parent = parent;
        }
    }

    /// <summary>
    /// In-memory recording, handed to <see cref="GamcWriter"/>.
    /// One float per transform component:
    /// [px, py, pz, qx, qy, qz, qw, sx, sy, sz] in Unity world space.
    /// </summary>
    [Serializable]
    public sealed class MocapDocument
    {
        public string createdUtc = "";
        public string room = "NoRoom";
        public int fps = 30;
        public ushort flags = GamcWriter.FlagFullSkeleton;
        public int frameCount;
        public float duration;
        public List<BoneDef> bones = new List<BoneDef>();
        public List<float> times = new List<float>();
        public List<float[]> frames = new List<float[]>();
    }
}
