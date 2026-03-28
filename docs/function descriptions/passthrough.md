Passthrough (overlay mode) makes the background transparent and changes it to either a computer-use screenshot/stream (webcall style - select a specific running app or just the desktop as is) or a camera feed (webcam, VR passthrough, XR glasses, etc.).

Float can be given access to either or both of these feeds. See live_mode.md for explanation of the streaming process. The general instruction is to quietly watch and make/prepare memory updates, calendar events, and keep the user on-track; or to proactively act as an agent controlling the computer. This would necessitate different workflow definitions to be made as we continue. See openai CUA and live voice agents repos on github for an example of how these are implemented via API.

While in this mode, the chat would be moved to one of the side bars, the top bar would hide if not hovered over, and the chat entry would be hidden by default. This necessitates some of the other UI functions be finished but could be hardcoded to replace the list of chats by default.

#related feature/ ui spec: Ideally, the tabs in the top bar would be drag-and-droppable to the sidebars, which could be tabbed, and could be pulled into the main window view. This is partly to enable passthrough mode: the sidebars can hold the important chat content while displaying the app view/camera view, if you want a closer view of the agent console, you should be able to see it in the main window by dragging it onto the top bar (which would automatically navigate to the tab, when a tab is dragged to another window.) so the active areas would be left, top, right, and defaults are still set as is, and the menus available would be knowledge/settings/chat/history/console

passthrough mode can toggle can be put near the auto/thinking/fast mode setting in chat entry box, as a camera/monitor/message icon. a small arrow on the bottom right opens a dropdown with available sources.

