using System;
using System.IO;
using System.Text;

namespace GorillaAnimate
{
    /// <summary>
    /// Writes a recording as a .gamc file (Gorilla Animate Motion Capture).
    ///
    /// Layout (little-endian):
    ///   char[4]  "GAMC"
    ///   uint16   version (1)
    ///   uint16   fps
    ///   uint32   frameCount
    ///   uint16   boneCount
    ///   uint16   flags (1 = the whole skinned skeleton was sampled)
    ///   string   createdUtc   (uint16 byte length + UTF-8)
    ///   string   room         (uint16 byte length + UTF-8)
    ///   boneCount x { string name, int16 parent }
    ///   frameCount x float32 times
    ///   frameCount x boneCount x float32[10] frames
    ///
    /// Each frame value is [px, py, pz, qx, qy, qz, qw, sx, sy, sz] in Unity
    /// world space, exactly what the recorder samples.
    /// </summary>
    internal static class GamcWriter
    {
        internal const ushort Version = 1;

        /// <summary>
        /// Set when every bone of the skinned skeleton was recorded (torso,
        /// arms, legs, fingers), so the Blender side can tell a complete file
        /// from the old 28-joint head+hands capture.
        /// </summary>
        internal const ushort FlagFullSkeleton = 1;

        private const int FloatsPerBone = 10;

        internal static byte[] Build(MocapDocument doc)
        {
            if (doc == null) throw new ArgumentNullException(nameof(doc));
            if (doc.bones == null || doc.bones.Count == 0) throw new InvalidOperationException("No bones");
            if (doc.frames == null || doc.frames.Count == 0) throw new InvalidOperationException("No frames");
            if (doc.bones.Count > ushort.MaxValue) throw new InvalidOperationException("Too many bones");
            if (doc.frames.Count > int.MaxValue) throw new InvalidOperationException("Too many frames");

            int boneCount = doc.bones.Count;
            int frameCount = doc.frames.Count;

            int capacity = 64 + doc.bones.Count * 32 + frameCount * (4 + boneCount * FloatsPerBone * 4);
            using (MemoryStream stream = new MemoryStream(capacity))
            using (BinaryWriter writer = new BinaryWriter(stream, new UTF8Encoding(false)))
            {
                writer.Write((byte)'G');
                writer.Write((byte)'A');
                writer.Write((byte)'M');
                writer.Write((byte)'C');
                writer.Write(Version);
                writer.Write((ushort)Clamp(doc.fps, 0, ushort.MaxValue));
                writer.Write((uint)frameCount);
                writer.Write((ushort)boneCount);
                writer.Write(doc.flags);

                WriteString(writer, doc.createdUtc);
                WriteString(writer, doc.room);

                for (int i = 0; i < boneCount; i++)
                {
                    BoneDef bone = doc.bones[i];
                    WriteString(writer, bone != null ? bone.name : "bone_" + i);
                    int parent = bone != null ? bone.parent : -1;
                    writer.Write((short)Clamp(parent, short.MinValue, short.MaxValue));
                }

                for (int f = 0; f < frameCount; f++)
                    writer.Write(f < doc.times.Count ? Clean(doc.times[f]) : f / (float)Math.Max(1, doc.fps));

                for (int f = 0; f < frameCount; f++)
                {
                    float[] frame = doc.frames[f];
                    for (int k = 0; k < boneCount * FloatsPerBone; k++)
                    {
                        float value = frame != null && k < frame.Length ? frame[k] : 0f;
                        writer.Write(Clean(value));
                    }
                }

                writer.Flush();
                return stream.ToArray();
            }
        }

        internal static string Extension => ".gamc";

        private static void WriteString(BinaryWriter writer, string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                writer.Write((ushort)0);
                return;
            }

            byte[] bytes = Encoding.UTF8.GetBytes(value);
            if (bytes.Length > ushort.MaxValue)
            {
                writer.Write((ushort)ushort.MaxValue);
                writer.Write(bytes, 0, ushort.MaxValue);
                return;
            }

            writer.Write((ushort)bytes.Length);
            writer.Write(bytes);
        }

        private static float Clean(float value)
        {
            if (float.IsNaN(value) || float.IsInfinity(value)) return 0f;
            return value;
        }

        private static int Clamp(int value, int min, int max)
        {
            if (value < min) return min;
            if (value > max) return max;
            return value;
        }
    }
}
