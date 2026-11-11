using UnityEngine;

namespace GorillaAnimate
{
    internal static class MenuUtil
    {
        internal static Font GetFont()
        {
            Font font = null;
            try { font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"); } catch { }
            if (font == null) { try { font = Resources.GetBuiltinResource<Font>("Arial.ttf"); } catch { } }
            if (font == null) { try { font = Font.CreateDynamicFontFromOSFont("Arial", 64); } catch { } }
            return font;
        }

        internal static Material MakeMaterial(Color color)
        {
            Shader shader = Shader.Find("GorillaTag/UberShader");
            if (shader == null) shader = Shader.Find("Unlit/Color");
            if (shader == null) shader = Shader.Find("Sprites/Default");
            if (shader == null) shader = Shader.Find("Standard");

            Material mat = new Material(shader);
            if (mat.HasProperty("_BaseColor")) mat.SetColor("_BaseColor", color);
            if (mat.HasProperty("_Color")) mat.SetColor("_Color", color);
            return mat;
        }

        /// <summary>The hand that holds the menu.</summary>
        internal static Transform LeftHand(VRRig rig)
        {
            try
            {
                if (rig != null && rig.leftHand != null && rig.leftHand.rigTarget != null)
                    return rig.leftHand.rigTarget;
            }
            catch { }

            return TriggerTransform(true);
        }

        internal static Transform RightHand(VRRig rig)
        {
            try
            {
                if (rig != null && rig.rightHand != null && rig.rightHand.rigTarget != null)
                    return rig.rightHand.rigTarget;
            }
            catch { }

            return TriggerTransform(false);
        }

        /// <summary>The right index fingertip if the rig exposes it, otherwise the right hand.</summary>
        internal static Transform RightPressPoint(VRRig rig)
        {
            try
            {
                if (rig != null && rig.rightIndex != null && rig.rightIndex.fingerBone3 != null)
                    return rig.rightIndex.fingerBone3;
            }
            catch { }

            return RightHand(rig);
        }

        private static Transform TriggerTransform(bool left)
        {
            try
            {
                GorillaTagger tagger = GorillaTagger.Instance;
                if (tagger != null)
                {
                    GameObject trigger = left ? tagger.leftHandTriggerCollider : tagger.rightHandTriggerCollider;
                    if (trigger != null) return trigger.transform;
                }
            }
            catch { }

            return null;
        }

        internal static string FormatTime(float seconds)
        {
            if (seconds < 0f) seconds = 0f;
            int total = Mathf.FloorToInt(seconds);
            int minutes = total / 60;
            int secs = total % 60;
            return minutes.ToString("00") + ":" + secs.ToString("00");
        }
    }
}
