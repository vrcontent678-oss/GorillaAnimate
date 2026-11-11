using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using BepInEx;
using Photon.Pun;
using UnityEngine;

namespace GorillaAnimate
{
    /// <summary>
    /// Samples the local player's Gorilla rig into world-space transforms.
    /// The data is intentionally self-contained: name, parent and transform per joint.
    /// </summary>
    internal sealed class MocapRecorder
    {
        private const int FloatsPerBone = 10;

        private readonly Plugin _plugin;
        private readonly List<BoneDef> _bones = new List<BoneDef>();
        private readonly List<Transform> _transforms = new List<Transform>();
        private readonly List<float[]> _frames = new List<float[]>();
        private readonly List<float> _times = new List<float>();

        private bool _recording;
        private bool _saving;
        private double _startTime;
        private double _nextSample;
        private float _lastDuration;
        private int _fps;
        private string _room = "NoRoom";
        private string _message = "Ready";
        private string _lastFile = "";

        internal MocapRecorder(Plugin plugin)
        {
            _plugin = plugin;
            _fps = Mathf.Clamp(plugin.Fps.Value, 10, 120);
        }

        internal bool IsRecording => _recording;
        internal bool IsSaving => _saving;
        internal int FrameCount => _frames.Count;
        internal int BoneCount => _bones.Count;
        internal int Fps => _fps;
        internal string Message => _message;
        internal string LastFile => _lastFile;
        internal float Elapsed => _recording ? (float)(Time.realtimeSinceStartup - _startTime) : _lastDuration;
        internal float Duration => _lastDuration;

        internal void Toggle()
        {
            if (_recording) Stop(true);
            else Start();
        }

        internal void CycleFps()
        {
            if (_recording)
            {
                _message = "Stop first";
                return;
            }

            int[] rates = { 24, 30, 60, 90 };
            int idx = Array.IndexOf(rates, _fps);
            _fps = rates[(idx + 1) % rates.Length];
            _plugin.Fps.Value = _fps;
            _message = "FPS " + _fps;
        }

        internal void Start()
        {
            if (_saving)
            {
                _message = "Still saving...";
                return;
            }

            VRRig rig;
            try { rig = VRRig.LocalRig; }
            catch { rig = null; }

            if (rig == null)
            {
                _message = "Join a room first";
                return;
            }

            if (!BuildBones(rig))
            {
                _message = "No bones found";
                return;
            }

            _frames.Clear();
            _times.Clear();
            _room = GetRoomName();
            _fps = Mathf.Clamp(_plugin.Fps.Value, 10, 120);
            _startTime = Time.realtimeSinceStartup;
            _nextSample = _startTime;
            _lastDuration = 0f;
            _lastFile = "";
            _recording = true;
            _message = "Recording " + _fps + "fps";
            _plugin.LogInfo("Gorilla Animate: recording started (" + _fps + " fps, " + _bones.Count + " joints)");
            try
            {
                _plugin.StartCoroutine(SampleLoop());
            }
            catch (Exception ex)
            {
                _recording = false;
                _message = "Could not start sampler";
                _plugin.LogError("Gorilla Animate: could not start the end-of-frame sampler: " + ex);
            }
        }

        /// <summary>
        /// Samples once per rendered frame, after every Update and LateUpdate
        /// has run. Reading transforms from Update can catch the rig half way
        /// through its own update: the head is this frame's pose while the
        /// hands are still last frame's, and the mismatch looks exactly like
        /// hands and fingers spinning faster than they really moved.
        /// </summary>
        private IEnumerator SampleLoop()
        {
            while (_recording)
            {
                yield return new WaitForEndOfFrame();
                if (!_recording) break;
                Tick();
            }
        }

        internal void Stop(bool save)
        {
            if (!_recording) return;

            _recording = false;
            _lastDuration = (float)(Time.realtimeSinceStartup - _startTime);
            _message = "Stopped " + _frames.Count + "f";

            if (save && _frames.Count > 1) SaveNow();
        }

        internal void Tick()
        {
            if (!_recording) return;

            VRRig rig;
            try { rig = VRRig.LocalRig; }
            catch { rig = null; }

            if (rig == null)
            {
                Stop(true);
                _message = "Rig lost - saved";
                return;
            }

            double now = Time.realtimeSinceStartup;
            float maxSeconds = _plugin.MaxMinutes.Value * 60f;
            if (now - _startTime >= maxSeconds)
            {
                Stop(true);
                _message = "Max length - saved";
                return;
            }

            double interval = 1.0 / _fps;
            if (now >= _nextSample)
            {
                Sample();
                _nextSample = now + interval;
            }
        }

        private void Sample()
        {
            float[] frame = new float[_transforms.Count * FloatsPerBone];
            for (int i = 0; i < _transforms.Count; i++)
            {
                Transform t = _transforms[i];
                if (t == null) continue;

                int o = i * FloatsPerBone;
                Vector3 p = t.position;
                Quaternion q = t.rotation;
                Vector3 s = t.lossyScale;

                frame[o + 0] = p.x; frame[o + 1] = p.y; frame[o + 2] = p.z;
                frame[o + 3] = q.x; frame[o + 4] = q.y; frame[o + 5] = q.z; frame[o + 6] = q.w;
                frame[o + 7] = s.x; frame[o + 8] = s.y; frame[o + 9] = s.z;
            }

            _frames.Add(frame);
            _times.Add((float)(Time.realtimeSinceStartup - _startTime));
        }

        internal void SaveNow()
        {
            if (_saving || _frames.Count < 2)
            {
                if (_frames.Count < 2 && !_saving) _message = "Nothing saved";
                return;
            }

            List<BoneDef> boneCopy = new List<BoneDef>(_bones.Count);
            for (int i = 0; i < _bones.Count; i++) boneCopy.Add(new BoneDef(_bones[i].name, _bones[i].parent));

            MocapDocument doc = new MocapDocument
            {
                createdUtc = DateTime.UtcNow.ToString("o"),
                room = _room,
                fps = _fps,
                flags = GamcWriter.FlagFullSkeleton,
                frameCount = _frames.Count,
                duration = _times.Count > 0 ? _times[_times.Count - 1] : _frames.Count / (float)_fps,
                bones = boneCopy,
                times = new List<float>(_times),
                frames = new List<float[]>(_frames)
            };

            string room = Sanitize(_room);
            string date = DateTime.Now.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
            string dir = Path.Combine(Paths.PluginPath, "GorillaAnimate", "Recordings", room, date);
            Directory.CreateDirectory(dir);
            string fileName = "mocap_" + DateTime.Now.ToString("yyyyMMdd_HHmmss_fff", CultureInfo.InvariantCulture) + ".gamc";
            string fullPath = Path.Combine(dir, fileName);

            _saving = true;
            _message = "Saving " + _frames.Count + "f...";
            _plugin.LogInfo("Gorilla Animate: saving " + fullPath);

            float span = doc.duration;
            float rate = span > 0.001f ? (doc.frameCount - 1) / span : _fps;
            _plugin.LogInfo(
                "Gorilla Animate: " + doc.frameCount + " samples over " + span.ToString("0.00", CultureInfo.InvariantCulture) +
                " s = " + rate.ToString("0.0", CultureInfo.InvariantCulture) + " Hz (target " + _fps +
                ", " + doc.bones.Count + " joints)");

            Task.Run(() =>
            {
                try
                {
                    File.WriteAllBytes(fullPath, GamcWriter.Build(doc));

                    _lastFile = fullPath;
                    _message = "Saved .gamc";
                    _plugin.LogInfo("Gorilla Animate: saved " + fullPath);
                }
                catch (Exception ex)
                {
                    _message = "Save failed!";
                    _plugin.LogError("Gorilla Animate save failed: " + ex);
                }
                finally
                {
                    _saving = false;
                }
            });
        }

        private bool BuildBones(VRRig rig)
        {
            _bones.Clear();
            _transforms.Clear();

            Dictionary<Transform, int> map = new Dictionary<Transform, int>();

            void Add(string preferredName, Transform t)
            {
                if (t == null) return;
                if (map.ContainsKey(t)) return;

                // Always use the semantic name (hand.L, thumb.01.L, ...) so the
                // Blender add-on can map joints on any gorilla rig without
                // depending on Unity transform names.
                string name = string.IsNullOrEmpty(preferredName) ? "bone" : preferredName;
                // Blender bone names must be unique.
                string unique = name;
                int suffix = 2;
                while (_bones.Exists(b => b.name == unique))
                    unique = name + "_" + (suffix++);

                map[t] = _bones.Count;
                _bones.Add(new BoneDef(unique, -1));
                _transforms.Add(t);
            }

            void AddAny(string preferredName, params string[] candidates)
            {
                for (int i = 0; i < candidates.Length; i++)
                {
                    Transform t = FindDeep(rig.transform, candidates[i]);
                    if (t != null)
                    {
                        Add(preferredName, t);
                        return;
                    }
                }
            }

            // Index 0 must be the rig root: the add-on uses it for root motion.
            Add("root", rig.transform);

            // Semantic names for the joints we can identify exactly.
            Dictionary<Transform, string> known = new Dictionary<Transform, string>();
            void Mark(Transform t, string name)
            {
                if (t != null && !known.ContainsKey(t)) known[t] = name;
            }

            Mark(rig.bodyTransform, "body");
            Mark(rig.head != null ? rig.head.rigTarget : null, "head");
            Mark(rig.leftHand != null ? rig.leftHand.rigTarget : null, "hand.L");
            Mark(rig.rightHand != null ? rig.rightHand.rigTarget : null, "hand.R");

            Mark(rig.leftThumb != null ? rig.leftThumb.fingerBone1 : null, "thumb.01.L");
            Mark(rig.leftThumb != null ? rig.leftThumb.fingerBone2 : null, "thumb.02.L");
            Mark(rig.leftIndex != null ? rig.leftIndex.fingerBone1 : null, "index.01.L");
            Mark(rig.leftIndex != null ? rig.leftIndex.fingerBone2 : null, "index.02.L");
            Mark(rig.leftIndex != null ? rig.leftIndex.fingerBone3 : null, "index.03.L");
            Mark(rig.leftMiddle != null ? rig.leftMiddle.fingerBone1 : null, "middle.01.L");
            Mark(rig.leftMiddle != null ? rig.leftMiddle.fingerBone2 : null, "middle.02.L");
            Mark(rig.leftMiddle != null ? rig.leftMiddle.fingerBone3 : null, "middle.03.L");

            Mark(rig.rightThumb != null ? rig.rightThumb.fingerBone1 : null, "thumb.01.R");
            Mark(rig.rightThumb != null ? rig.rightThumb.fingerBone2 : null, "thumb.02.R");
            Mark(rig.rightIndex != null ? rig.rightIndex.fingerBone1 : null, "index.01.R");
            Mark(rig.rightIndex != null ? rig.rightIndex.fingerBone2 : null, "index.02.R");
            Mark(rig.rightIndex != null ? rig.rightIndex.fingerBone3 : null, "index.03.R");
            Mark(rig.rightMiddle != null ? rig.rightMiddle.fingerBone1 : null, "middle.01.R");
            Mark(rig.rightMiddle != null ? rig.rightMiddle.fingerBone2 : null, "middle.02.R");
            Mark(rig.rightMiddle != null ? rig.rightMiddle.fingerBone3 : null, "middle.03.R");

            // The visible gorilla skeleton is the ground truth. Recording every
            // bone in it is what captures the torso, arms, legs and the real
            // posed hands instead of a guess at where they are.
            SkinnedMeshRenderer skin = rig.mainSkin;
            if (skin != null && skin.bones != null && skin.bones.Length > 0)
            {
                for (int i = 0; i < skin.bones.Length; i++)
                {
                    Transform bone = skin.bones[i];
                    if (bone == null) continue;

                    string name;
                    if (!known.TryGetValue(bone, out name)) name = bone.name;
                    Add(name, bone);
                }
            }

            // VR targets normally live inside the skeleton, but keep any that
            // did not show up there.
            foreach (KeyValuePair<Transform, string> pair in known)
                Add(pair.Value, pair.Key);

            // Last resort for rigs without a usable mainSkin (posed hands are
            // normally already covered by the loop above).
            AddAny("spine", "spine", "spine1", "spine.001", "Spine", "torso");
            AddAny("chest", "chest", "spine2", "spine.002", "Chest");
            AddAny("neck", "neck", "Neck");
            AddAny("shoulder.L", "shoulder.L", "shoulder_L", "LeftShoulder", "clavicle.L", "Shoulder.L");
            AddAny("upper_arm.L", "upper_arm.L", "upperarm.L", "LeftUpperArm", "LeftArm", "arm.L");
            AddAny("forearm.L", "forearm.L", "lower_arm.L", "LeftForeArm", "LeftLowerArm", "elbow.L");
            AddAny("shoulder.R", "shoulder.R", "shoulder_R", "RightShoulder", "clavicle.R", "Shoulder.R");
            AddAny("upper_arm.R", "upper_arm.R", "upperarm.R", "RightUpperArm", "RightArm", "arm.R");
            AddAny("forearm.R", "forearm.R", "lower_arm.R", "RightForeArm", "RightLowerArm", "elbow.R");

            for (int i = 0; i < _transforms.Count; i++)
            {
                Transform p = _transforms[i].parent;
                int parent = -1;
                while (p != null)
                {
                    if (map.TryGetValue(p, out int idx) && idx != i)
                    {
                        parent = idx;
                        break;
                    }
                    p = p.parent;
                }
                _bones[i].parent = parent;
            }

            LogBones(rig);
            return _bones.Count > 3;
        }

        /// <summary>
        /// One line with every recorded joint, so the Blender mapping can be
        /// checked against the real rig without opening the game again.
        /// </summary>
        private void LogBones(VRRig rig)
        {
            SkinnedMeshRenderer skin = rig.mainSkin;
            int skinBones = skin != null && skin.bones != null ? skin.bones.Length : 0;

            StringBuilder sb = new StringBuilder();
            sb.Append("Gorilla Animate: ").Append(_bones.Count)
              .Append(" joints recorded (mainSkin bones: ").Append(skinBones).Append("):");
            for (int i = 0; i < _bones.Count; i++)
                sb.Append(' ').Append(_bones[i].name);

            _plugin.LogInfo(sb.ToString());
        }

        private static Transform FindDeep(Transform root, string name)
        {
            if (root == null) return null;
            Stack<Transform> stack = new Stack<Transform>();
            stack.Push(root);
            while (stack.Count > 0)
            {
                Transform t = stack.Pop();
                if (t.name == name) return t;
                for (int i = t.childCount - 1; i >= 0; i--) stack.Push(t.GetChild(i));
            }
            return null;
        }

        private static string GetRoomName()
        {
            try
            {
                if (PhotonNetwork.InRoom && PhotonNetwork.CurrentRoom != null)
                    return PhotonNetwork.CurrentRoom.Name;
            }
            catch { }
            return "NoRoom";
        }

        private static string Sanitize(string value)
        {
            if (string.IsNullOrEmpty(value)) return "NoRoom";
            value = Regex.Replace(value, "[^A-Za-z0-9_-]", "_");
            return value.Length > 24 ? value.Substring(0, 24) : value;
        }
    }
}
