using System;
using UnityEngine;

namespace GorillaAnimate
{
    /// <summary>
    /// A physical, world-space "mod menu" held in the player's left hand.
    /// It is opened and closed with the left controller Y button and its buttons are
    /// pressed with the right index fingertip.
    /// </summary>
    internal sealed class AnimateMenu : MonoBehaviour
    {
        private const int PanelLayer = 2; // Ignore Raycast - keeps the menu out of gameplay raycasts.

        private Plugin _plugin;
        private GameObject _content;

        private TextMesh _statusText;
        private TextMesh _recText;
        private TextMesh _fpsText;
        private Material _recMaterial;
        private Material _fpsMaterial;

        private bool _open;
        private bool _built;
        private bool _placed;
        private Rect _guiRect = new Rect(24f, 24f, 320f, 300f);

        internal bool IsOpen => _open;

        internal static AnimateMenu Create(Plugin plugin)
        {
            GameObject go = new GameObject("GorillaAnimateMenu");
            go.transform.position = new Vector3(0f, -1000f, 0f);
            DontDestroyOnLoad(go);
            AnimateMenu menu = go.AddComponent<AnimateMenu>();
            menu._plugin = plugin;
            menu.Build();
            return menu;
        }

        private void Build()
        {
            if (_built) return;
            _built = true;

            _content = new GameObject("Content");
            _content.transform.SetParent(transform, false);

            GameObject panel = GameObject.CreatePrimitive(PrimitiveType.Cube);
            panel.name = "Panel";
            panel.layer = PanelLayer;
            panel.transform.SetParent(_content.transform, false);
            panel.transform.localScale = new Vector3(0.26f, 0.32f, 0.01f);
            RemoveCollider(panel);
            panel.GetComponent<Renderer>().material = MenuUtil.MakeMaterial(new Color(0.012f, 0.018f, 0.028f, 1f));

            MakeText("Title", "GORILLA ANIMATE", _content.transform, new Vector3(0f, 0.132f, 0.010f), 0.018f, new Color(0.35f, 1f, 0.72f));
            _statusText = MakeText("Status", "Ready", _content.transform, new Vector3(0f, 0.098f, 0.010f), 0.012f, new Color(0.92f, 0.96f, 1f));

            _recMaterial = MenuUtil.MakeMaterial(new Color(0.10f, 0.72f, 0.34f, 1f));
            _recText = MakeButton("RecordButton", "RECORD", new Vector3(0f, 0.045f, 0.006f), 0.21f, 0.052f, _recMaterial, () => _plugin.Recorder.Toggle());

            _fpsMaterial = MenuUtil.MakeMaterial(new Color(0.34f, 0.27f, 0.62f, 1f));
            MakeButton("SaveButton", "SAVE", new Vector3(0f, -0.015f, 0.006f), 0.21f, 0.052f,
                MenuUtil.MakeMaterial(new Color(0.12f, 0.34f, 0.72f, 1f)), () => _plugin.Recorder.SaveNow());

            _fpsText = MakeButton("FpsButton", "FPS  30", new Vector3(0f, -0.075f, 0.006f), 0.21f, 0.052f, _fpsMaterial, () => _plugin.Recorder.CycleFps());

            MakeText("Footer", "Y CLOSE   F8 REC   F9 SAVE   F10 FPS", _content.transform, new Vector3(0f, -0.128f, 0.010f), 0.010f, new Color(0.55f, 0.62f, 0.72f));

            _open = _plugin.ShowMenu.Value;
            _content.SetActive(_open);
            _plugin.LogInfo("Gorilla Animate menu built.");
        }

        internal void ToggleOpen()
        {
            SetOpen(!_open);
        }

        internal void SetOpen(bool open)
        {
            bool changed = open != _open;
            _open = open;
            if (_content != null) _content.SetActive(open);

            if (open && changed)
            {
                _placed = false;
                _plugin.LogInfo("Gorilla Animate menu opened.");
                try
                {
                    GorillaTagger tagger = GorillaTagger.Instance;
                    if (tagger != null) tagger.StartVibration(true, 0.2f, 0.04f);
                }
                catch { }
            }
        }

        private void Update()
        {
            if (_plugin == null || !_built) return;

            VRRig rig = null;
            try { rig = VRRig.LocalRig; }
            catch { }

            Transform head = rig != null && rig.head != null ? rig.head.rigTarget : null;
            Transform hand = rig != null ? MenuUtil.LeftHand(rig) : null;
            Transform body = rig != null ? rig.bodyTransform : null;

            bool canShow = head != null && (hand != null || body != null);
            if (_content != null) _content.SetActive(_open && canShow);

            if (!_open || !canShow)
            {
                _placed = false;
                return;
            }

            float scale = _plugin.MenuScale.Value;
            Vector3 target = hand != null ? HandAnchor(hand, scale) : ChestAnchor(body, head, scale);

            // Keep the panel out of the player's face if they raise their hand too high.
            Vector3 fromHead = target - head.position;
            float minDistance = 0.32f;
            if (fromHead.magnitude < minDistance)
            {
                Vector3 dir = fromHead.sqrMagnitude > 0.0001f ? fromHead.normalized : -head.forward;
                target = head.position + dir * minDistance;
            }

            Vector3 look = head.position - target;
            Quaternion wanted = look.sqrMagnitude > 0.0001f
                ? FaceHead(target, head)
                : transform.rotation;

            if (!_placed)
            {
                transform.position = target;
                transform.rotation = wanted;
                _placed = true;
            }
            else
            {
                float k = 1f - Mathf.Exp(-28f * Time.unscaledDeltaTime);
                transform.position = Vector3.Lerp(transform.position, target, k);

                if ((head.position - transform.position).sqrMagnitude > 0.0001f)
                {
                    Quaternion wantedNow = FaceHead(transform.position, head);
                    transform.rotation = Quaternion.Slerp(transform.rotation, wantedNow, k);
                }
            }

            transform.localScale = new Vector3(scale, scale, scale);

            MocapRecorder rec = _plugin.Recorder;
            if (rec.IsSaving)
            {
                _statusText.text = "SAVING...";
            }
            else if (rec.IsRecording)
            {
                _statusText.text = "REC " + MenuUtil.FormatTime(rec.Elapsed) + "  |  " + rec.FrameCount + "f";
            }
            else
            {
                _statusText.text = rec.Message;
            }

            _recText.text = rec.IsRecording ? "STOP + SAVE" : "RECORD";
            SetMatColor(_recMaterial, rec.IsRecording ? new Color(0.78f, 0.10f, 0.10f, 1f) : new Color(0.10f, 0.72f, 0.34f, 1f));
            _fpsText.text = "FPS  " + rec.Fps;
        }

        /// <summary>
        /// Holds the panel just above the left hand, like a hand-held tablet.
        /// The offset grows with the menu scale so the hand never covers the lowest row.
        /// </summary>
        private static Vector3 HandAnchor(Transform hand, float scale)
        {
            return hand.position + Vector3.up * (0.16f * scale + 0.07f);
        }

        private static Vector3 ChestAnchor(Transform body, Transform head, float scale)
        {
            Vector3 forward = body.forward;
            forward.y = 0f;
            if (forward.sqrMagnitude < 0.0001f)
            {
                forward = head.forward;
                forward.y = 0f;
            }
            if (forward.sqrMagnitude < 0.0001f) forward = Vector3.forward;
            forward.Normalize();

            return body.position + forward * (0.34f * scale) + Vector3.up * 0.055f;
        }

        /// <summary>
        /// Rotation that makes the panel's +Z point at the player's head, with a safe
        /// fallback when the panel is almost directly above the head.
        /// </summary>
        private static Quaternion FaceHead(Vector3 position, Transform head)
        {
            Vector3 forward = head.position - position;
            if (forward.sqrMagnitude < 0.0001f) return head.rotation;
            forward.Normalize();

            if (Mathf.Abs(Vector3.Dot(forward, Vector3.up)) > 0.985f)
            {
                forward = head.forward;
                forward.y = 0f;
                if (forward.sqrMagnitude < 0.0001f) forward = Vector3.forward;
                forward.Normalize();
            }

            return Quaternion.LookRotation(forward, Vector3.up);
        }

        private static void SetMatColor(Material mat, Color color)
        {
            if (mat == null) return;
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", color);
            if (mat.HasProperty("_Color")) mat.SetColor("_Color", color);
        }

        private static void RemoveCollider(GameObject go)
        {
            Collider collider = go.GetComponent<Collider>();
            if (collider == null) return;
            collider.enabled = false;
            Destroy(collider);
        }

        private TextMesh MakeButton(string name, string label, Vector3 localPos, float width, float height,
            Material material, Action onPress, Transform parent = null)
        {
            Transform root = parent != null ? parent : _content.transform;

            GameObject holder = new GameObject(name);
            holder.transform.SetParent(root, false);
            holder.transform.localPosition = localPos;

            GameObject cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
            cube.name = "Cube";
            cube.layer = PanelLayer;
            cube.transform.SetParent(holder.transform, false);
            cube.transform.localScale = new Vector3(width, height, 0.012f);
            RemoveCollider(cube);
            cube.GetComponent<Renderer>().material = material;

            cube.AddComponent<AnimateButton>().Init(onPress);

            return MakeText(name + "Label", label, holder.transform, new Vector3(0f, 0f, 0.008f), height * 0.34f, Color.white);
        }

        private TextMesh MakeText(string name, string text, Transform parent, Vector3 localPos, float targetHeight, Color color)
        {
            GameObject go = new GameObject(name);
            go.layer = PanelLayer;
            go.transform.SetParent(parent, false);
            go.transform.localPosition = localPos;
            go.transform.localScale = Vector3.one;
            // Legacy TextMesh is readable from its local -Z. The menu's +Z faces the
            // player, so flip the text around Y to keep it readable and not mirrored.
            go.transform.localRotation = Quaternion.Euler(0f, 180f, 0f);

            TextMesh mesh = go.AddComponent<TextMesh>();
            mesh.font = MenuUtil.GetFont();
            mesh.text = text;
            mesh.anchor = TextAnchor.MiddleCenter;
            mesh.alignment = TextAlignment.Center;
            mesh.fontSize = 72;
            mesh.characterSize = targetHeight;
            mesh.color = color;

            MeshRenderer renderer = go.GetComponent<MeshRenderer>();
            if (mesh.font != null)
            {
                renderer.material = mesh.font.material;
                // Fit the rendered text to the requested world height.
                float measured = renderer.bounds.size.y;
                if (measured > 0.0005f)
                {
                    float factor = Mathf.Clamp(targetHeight / measured, 0.01f, 100f);
                    go.transform.localScale = Vector3.one * factor;
                }
            }

            return mesh;
        }

        private void OnGUI()
        {
            if (_plugin == null) return;
            if (!_plugin.DesktopGui.Value) return;

            _guiRect = GUILayout.Window(947231, _guiRect, DrawWindow, "Gorilla Animate");
        }

        private void DrawWindow(int id)
        {
            MocapRecorder rec = _plugin.Recorder;

            GUILayout.Label("In-world menu: " + (_open ? "open" : "closed") + "   (left controller Y)");
            GUILayout.Label("Status: " + rec.Message);
            GUILayout.Label("Frames: " + rec.FrameCount + "    FPS: " + rec.Fps + "    Joints: " + rec.BoneCount);

            if (GUILayout.Button(rec.IsRecording ? "Stop + Save (F8)" : "Start recording (F8)"))
                rec.Toggle();

            if (GUILayout.Button("Save now (F9)"))
                rec.SaveNow();

            if (GUILayout.Button("Cycle FPS (F10)"))
                rec.CycleFps();

            if (GUILayout.Button(_open ? "Close in-world menu (Y)" : "Open in-world menu (Y)"))
                ToggleOpen();

            if (!string.IsNullOrEmpty(rec.LastFile))
                GUILayout.Label("Last file:\n" + rec.LastFile);

            GUI.DragWindow();
        }
    }
}
