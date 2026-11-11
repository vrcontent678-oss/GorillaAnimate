using BepInEx;
using BepInEx.Configuration;
using UnityEngine;
using UnityEngine.InputSystem;

namespace GorillaAnimate
{
    /// <summary>
    /// Entry point. Builds the hand-held menu and drives the recorder.
    /// </summary>
    [BepInPlugin(Guid, PluginName, VersionString)]
    public sealed class Plugin : BaseUnityPlugin
    {
        public const string Guid = "com.rayan.gorillaanimate";
        public const string PluginName = "Gorilla Animate";
        public const string VersionString = "1.1.0";

        internal static Plugin Instance { get; private set; }

        internal ConfigEntry<int> Fps;
        internal ConfigEntry<int> MaxMinutes;
        internal ConfigEntry<bool> ShowMenu;
        internal ConfigEntry<float> MenuScale;
        internal ConfigEntry<bool> DesktopGui;

        internal MocapRecorder Recorder;
        internal AnimateMenu Menu;

        private bool _yHeld;

        internal void LogInfo(string message)
        {
            Logger.LogInfo(message);
        }

        internal void LogError(string message)
        {
            Logger.LogError(message);
        }

        private void Awake()
        {
            Instance = this;

            Fps = Config.Bind(
                "Recording", "FPS", 30,
                new ConfigDescription(
                    "Mocap sample rate. 30 is a good balance for Blender; 60 is smoother but heavier.",
                    new AcceptableValueRange<int>(10, 120)));

            MaxMinutes = Config.Bind(
                "Recording", "MaxMinutes", 10,
                new ConfigDescription(
                    "Recording stops automatically after this many minutes to protect memory.",
                    new AcceptableValueRange<int>(1, 60)));

            ShowMenu = Config.Bind("Menu", "ShowMenu", true, "Start with the in-world menu open. The left controller Y button toggles it.");
            MenuScale = Config.Bind(
                "Menu", "Scale", 1.0f,
                new ConfigDescription("Scale of the in-world menu.", new AcceptableValueRange<float>(0.5f, 2.5f)));
            DesktopGui = Config.Bind("Menu", "DesktopGui", true, "Also draw a small desktop window (F6 toggles it).");

            Recorder = new MocapRecorder(this);
            Logger.LogInfo("Gorilla Animate " + VersionString + " loaded. Left controller Y toggles the menu. F8 record, F9 save.");
        }

        private void Update()
        {
            // The recorder samples from its own end-of-frame coroutine, so the
            // pose it reads is the one the frame actually rendered.
            if (Menu == null)
            {
                VRRig rig = null;
                try { rig = VRRig.LocalRig; }
                catch { }
                if (rig != null) Menu = AnimateMenu.Create(this);
            }

            HandleKeyboard();
            PollMenuButton();
        }

        /// <summary>
        /// Left controller Y (the "secondary" button) toggles the in-world menu.
        /// Edge detection keeps a held button from reopening it every frame.
        /// </summary>
        private void PollMenuButton()
        {
            bool y = false;
            try
            {
                ControllerInputPoller poller = ControllerInputPoller.instance;
                if (poller != null) y = poller.leftControllerSecondaryButton;
            }
            catch { }

            if (y && !_yHeld && Menu != null)
                Menu.ToggleOpen();

            _yHeld = y;
        }

        private void HandleKeyboard()
        {
            Keyboard kb;
            try { kb = Keyboard.current; }
            catch { return; }
            if (kb == null) return;

            if (Pressed(kb, Key.F8)) Recorder.Toggle();
            if (Pressed(kb, Key.F9)) Recorder.SaveNow();
            if (Pressed(kb, Key.F10)) Recorder.CycleFps();
            if (Pressed(kb, Key.F7)) Menu?.ToggleOpen();
            if (Pressed(kb, Key.F6)) DesktopGui.Value = !DesktopGui.Value;
        }

        private static bool Pressed(Keyboard keyboard, Key key)
        {
            try
            {
                var control = keyboard[key];
                return control != null && control.wasPressedThisFrame;
            }
            catch
            {
                return false;
            }
        }
    }
}
