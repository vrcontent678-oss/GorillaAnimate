using System;
using UnityEngine;

namespace GorillaAnimate
{
    /// <summary>
    /// Touch button. The menu is held in the left hand, so the right index fingertip
    /// presses the buttons. A plain proximity test is used instead of physics triggers
    /// so the menu does not depend on collision layers or rigidbody setup.
    /// </summary>
    internal sealed class AnimateButton : MonoBehaviour
    {
        private Action _onPress;
        private float _lastPress = -10f;
        private static float _lastAnyPress = -10f;
        private bool _inside;

        internal void Init(Action onPress)
        {
            _onPress = onPress;
        }

        private void Update()
        {
            VRRig rig;
            try { rig = VRRig.LocalRig; }
            catch { rig = null; }

            Transform point = rig != null ? MenuUtil.RightPressPoint(rig) : null;
            bool inside = point != null && Touching(point);

            if (inside && !_inside)
            {
                _inside = true;
                Press();
            }
            else if (!inside)
            {
                _inside = false;
            }
        }

        private void OnDisable()
        {
            _inside = false;
        }

        private void Press()
        {
            float now = Time.unscaledTime;
            if (now - _lastPress < 0.35f) return;
            if (now - _lastAnyPress < 0.35f) return;
            _lastPress = now;
            _lastAnyPress = now;

            try { _onPress?.Invoke(); }
            catch (Exception ex) { Debug.LogException(ex); }

            try
            {
                GorillaTagger tagger = GorillaTagger.Instance;
                if (tagger != null)
                    tagger.StartVibration(false, 0.25f, 0.05f);
            }
            catch { }
        }

        /// <summary>
        /// Checks the fingertip against the button's local box, with a little padding
        /// so the button reacts before the finger visually clips through it.
        /// </summary>
        private bool Touching(Transform point)
        {
            Vector3 local = transform.InverseTransformPoint(point.position);
            Vector3 scale = transform.lossyScale;

            float padX = 0.030f / Mathf.Max(0.0001f, Mathf.Abs(scale.x));
            float padY = 0.014f / Mathf.Max(0.0001f, Mathf.Abs(scale.y));
            float padZ = 0.045f / Mathf.Max(0.0001f, Mathf.Abs(scale.z));

            return Mathf.Abs(local.x) <= 0.5f + padX
                && Mathf.Abs(local.y) <= 0.5f + padY
                && local.z >= -1.0f
                && local.z <= 0.5f + padZ;
        }
    }
}
