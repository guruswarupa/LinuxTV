import QtQuick 2.15

// Root of the LinuxTV home screen. Sized to the hosting QQuickWidget
// (SizeRootObjectToView). Holds no navigation logic of its own -- every
// D-pad/remote action is forwarded to HomeBackend, which is the single
// source of truth for what's selected; this file only reacts to it.
Item {
    id: root

    function currentEntry() {
        if (!backend.rows || backend.rows.length === 0) return null
        var row = backend.rows[backend.currentRow]
        if (!row || !row.apps) return null
        return row.apps[backend.currentCol] || null
    }

    property color currentColor: {
        var e = currentEntry()
        if (e && backend.art[e.key]) return backend.art[e.key].color
        return theme.bg
    }
    property string currentBackdrop: {
        var e = currentEntry()
        if (e && backend.art[e.key] && backend.art[e.key].backdrop) {
            return "file://" + backend.art[e.key].backdrop
        }
        return ""
    }

    AmbientBackground {
        anchors.fill: parent
        reduced: backend.reducedEffects
        targetColor: root.currentColor
        targetBackdrop: root.currentBackdrop
    }

    TopBar {
        id: topBar
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
    }

    Flickable {
        id: rowsFlick
        anchors.top: topBar.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.topMargin: 8
        contentWidth: width
        contentHeight: rowsColumn.height
        clip: true
        interactive: false
        focus: !backend.searchFocused && !backend.menuOpen && !backend.settingsOpen && backend.activePanel === ""

        function scrollToActiveRow() {
            // The very first row must always be fully visible at the top --
            // never push it up under the top bar to "preview" what's above,
            // since there's nothing above it.
            if (backend.currentRow === 0) {
                scrollAnim.stop()
                scrollAnim.from = rowsFlick.contentY
                scrollAnim.to = 0
                scrollAnim.start()
                return
            }
            var rowItem = rowRepeater.itemAt(backend.currentRow)
            if (!rowItem) return
            var target = rowItem.y - rowsFlick.height * 0.22
            target = Math.max(0, Math.min(target, Math.max(0, rowsFlick.contentHeight - rowsFlick.height)))
            scrollAnim.stop()
            scrollAnim.from = rowsFlick.contentY
            scrollAnim.to = target
            scrollAnim.start()
        }

        Component.onCompleted: scrollToActiveRow()

        NumberAnimation {
            id: scrollAnim
            target: rowsFlick
            property: "contentY"
            duration: 220
            easing.type: Easing.OutCubic
        }

        Connections {
            target: backend
            function onCurrentChanged() { rowsFlick.scrollToActiveRow() }
            function onRowsChanged() { rowsFlick.scrollToActiveRow() }
        }

        Column {
            id: rowsColumn
            width: rowsFlick.width
            spacing: 10

            Repeater {
                id: rowRepeater
                model: backend.rows
                delegate: AppRow {
                    width: rowsColumn.width
                    title: modelData.title
                    apps: modelData.apps
                    rowActive: index === backend.currentRow
                    activeCol: backend.currentCol
                    onCardActivated: (col) => {
                        backend.setCurrent(index, col)
                        backend.activate()
                    }
                    onCardContext: (col) => {
                        backend.setCurrent(index, col)
                        backend.openMenu()
                    }
                }
            }
        }

        Keys.onPressed: (event) => {
            switch (event.key) {
            case Qt.Key_Left:
                backend.navigate("LEFT")
                event.accepted = true
                break
            case Qt.Key_Right:
                backend.navigate("RIGHT")
                event.accepted = true
                break
            case Qt.Key_Up:
                backend.navigate("UP")
                event.accepted = true
                break
            case Qt.Key_Down:
                backend.navigate("DOWN")
                event.accepted = true
                break
            case Qt.Key_Return:
            case Qt.Key_Enter:
            case Qt.Key_Space:
                backend.activate()
                event.accepted = true
                break
            case Qt.Key_Escape:
            case Qt.Key_Back:
                backend.requestExit()
                event.accepted = true
                break
            case Qt.Key_Menu:
                backend.openMenu()
                event.accepted = true
                break
            default:
                event.accepted = false
            }
        }
    }

    Toast {
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 24
        anchors.horizontalCenter: parent.horizontalCenter
        width: 420
    }

    SettingsPanel {
        anchors.fill: parent
    }

    ContextMenu {
        anchors.fill: parent
    }

    LaunchSplash {
        anchors.fill: parent
    }

    AppPanel {
        anchors.fill: parent
    }
}
